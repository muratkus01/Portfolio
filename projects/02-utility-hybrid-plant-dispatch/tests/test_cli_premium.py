"""The market premium the command line hands the ladder: the realised market value that every
rung is settled on, per technology and period, and for B3 only a value it could know.

Offline: the data loader is replaced by synthetic calendar years and, where the test needs no
dispatch, the ladder by a recorder that solves nothing.
"""
from __future__ import annotations

import functools

import numpy as np
import pandas as pd
import pytest

import hybrid.baselines as baselines
import hybrid.cli as cli
from hybrid.config import MarketConfig, PlantConfig

YEAR = 2025
DEFAULT = MarketConfig()
PLANT = PlantConfig()
# Constant national factors: every market value is then the mean price of its period, and the
# default plant makes 25 MW of wind and 7.5 MW of PV in every step.
WIND_CF, PV_CF = 0.5, 0.25
WIND_SHARE = PLANT.wind_mw * WIND_CF / (PLANT.wind_mw * WIND_CF + PLANT.pv_mw * PV_CF)
# Price by Berlin month in every year, EUR/MWh: January 30, December 50, the others 60.
JANUARY, DECEMBER, OTHER = 30.0, 50.0, 60.0


def calendar_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """A Berlin calendar year of quarter-hours, cut to its first `days` days as load_year is."""
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    idx = pd.date_range(start, end, freq="15min", inclusive="left")
    month = idx.tz_convert("Europe/Berlin").month
    price = np.where(month == 1, JANUARY, np.where(month == 12, DECEMBER, OTHER))
    df = pd.DataFrame({"wind_cf": WIND_CF, "pv_cf": PV_CF, "pv_feed_in_cf": 0.8 * PV_CF,
                       "price_eur_mwh": price}, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


def plant_rate(market_value: float) -> float:
    """The default plant's rate when both technologies have this market value, EUR/MWh."""
    return (WIND_SHARE * max(0.0, DEFAULT.anzulegender_wert_wind - market_value)
            + (1 - WIND_SHARE) * max(0.0, DEFAULT.anzulegender_wert_pv - market_value))


def recording_ladder(calls: list):
    """Stand-in for `run_ladder` that records the premiums and B3's price-forecast error scale
    it is handed, and solves nothing."""
    def ladder(wind, pv, price, run, premium_rate, rebap=None, **kwargs):
        n = len(price)
        b3 = kwargs.get("b3_premium_rate")
        calls.append({"settle": np.broadcast_to(np.asarray(premium_rate, dtype=float), (n,)),
                      "b3": None if b3 is None else np.asarray(b3, dtype=float),
                      # read with a default so that code without the field fails on behaviour
                      "price_scale": getattr(run, "forecast_price_scale", None)})
        cost = {"net_revenue": 1.0, "export_mwh": 0.0, "chosen_curtail_mwh": 0.0,
                "forced_curtail_mwh": 0.0}
        return {name: {"cost": cost, "violations": {}} for name in
                ("B0 no battery", "B1 curtailment avoidance", "B2 perfect foresight")}
    return ladder


@pytest.mark.parametrize("command", ["ladder", "sizing", "decompose"])
def test_every_command_settles_on_the_annual_market_value_per_technology(monkeypatch,
                                                                         command):
    """Regression test: ladder, sizing and decompose paid max(0, AW wind - mean price): the
    wind value for both technologies, against a plain mean of the window's own prices.

    The default plant is paid against each technology's annual market value (Anlage 1 Nr. 2):
    the price weighted by that technology's feed-in over the whole year, here its mean. Wind
    and PV each get their own premium, and the plant's rate weights the two by its own
    generation.
    """
    calls = []
    monkeypatch.setattr(cli, "load_year", calendar_year)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder(calls))

    assert cli.main([command, "--year", str(YEAR), "--days", "2"]) == 0

    annual = (31 * JANUARY + 31 * DECEMBER + 303 * OTHER) / 365
    assert calls, "the command ran the ladder"
    for call in calls:
        assert call["settle"] == pytest.approx(np.full(2 * 96, plant_rate(annual)))


def test_b3_plans_each_month_on_the_realised_rate_of_the_month_before(monkeypatch):
    """Regression test: B3 planned on the premium it was settled on, built from prices of the
    window it had not seen yet.

    A month's market value is known only once the month is over, so B3 plans January on the
    realised rate of December of the year before and February on January's, while every rung
    is still settled on the annual value.
    """
    calls = []
    monkeypatch.setattr(cli, "load_year", calendar_year)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder(calls))

    assert cli.main(["ladder", "--year", str(YEAR), "--days", "40"]) == 0

    (call,) = calls
    assert call["b3"] is not None, "B3 was handed no premium of its own"
    january = 31 * 96
    assert call["b3"][:january] == pytest.approx(np.full(january, plant_rate(DECEMBER)))
    assert call["b3"][january:] == pytest.approx(np.full(9 * 96, plant_rate(JANUARY)))


def test_b3s_price_error_is_sized_on_the_year_before_the_window(monkeypatch):
    """Regression test: B3's price-forecast error was sized on the spread of the window's own
    prices, later ones included. It is sized now on the day-ahead prices of the year before,
    which B3 has seen when the window starts."""
    calls = []
    monkeypatch.setattr(cli, "load_year", calendar_year)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder(calls))

    assert cli.main(["ladder", "--year", str(YEAR), "--days", "2"]) == 0

    (call,) = calls
    assert call["price_scale"] is not None, "sized on the window's own prices"
    assert call["price_scale"] == pytest.approx(np.std(calendar_year(YEAR - 1)["price_eur_mwh"]))


def short_window(year: int, days: int | None = None, dt: float = 0.25,
                 later: float = 50.0) -> pd.DataFrame:
    """`calendar_year` whose first 16 steps are the window: 50 EUR/MWh, -10 in step 2, and
    `later` from step 12 to the end of January, so that it moves the realised January and
    annual values. Four hours keep B3's solves few, whatever `days` asks for."""
    df = calendar_year(year, dt=dt)
    if year == YEAR:
        january = np.full(31 * 96, 50.0)
        january[2], january[12:] = -10.0, later
        df.iloc[:31 * 96, df.columns.get_loc("price_eur_mwh")] = january
    return df if days is None else df.iloc[:16]


def recorded_ladder(results: list):
    """The real `run_ladder`, at the shipped forecast error, recording what it returns."""
    def ladder(*args, **kwargs):
        res = baselines.run_ladder(*args, **kwargs)
        results.append(res)
        return res
    return ladder


def test_later_prices_do_not_change_b3s_earlier_decisions(monkeypatch):
    """Regression test: prices B3 had not seen yet changed what it did before them. Its premium
    was built from the mean of the window's prices, and its price-forecast error was sized on
    their spread.

    Under the rule "none" the premium decides whether exporting through -10 EUR/MWh in step 2
    pays. Raising the rest of January from step 12 on, from 50 to 500 EUR/MWh, moves the
    realised January and annual values, which B2 plans on, by hundreds of EUR/MWh. It lies
    beyond the four-step horizon of steps 0 to 8, so it must not change B3's dispatch there,
    at the shipped forecast error.
    """
    dispatch = {}
    for later in (50.0, 500.0):
        results = []
        monkeypatch.setattr(cli, "load_year", functools.partial(short_window, later=later))
        monkeypatch.setattr(cli, "run_ladder", recorded_ladder(results))
        assert cli.main(["ladder", "--year", str(YEAR), "--days", "1", "--horizon", "4",
                         "--neg-rule", "none"]) == 0
        b3 = results[0]["B3 rolling MPC"]
        dispatch[later] = np.r_[b3["export"][:9], b3["p_bat"][:9]]
    assert dispatch[500.0] == pytest.approx(dispatch[50.0], abs=1e-6)

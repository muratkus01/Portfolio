"""The market premium the command line hands the ladder: the realised market value that every
rung is settled on, per technology and period.

Offline: the data loader is replaced by synthetic calendar years and the ladder by a recorder,
so no LP is solved.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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
    df = pd.DataFrame({"wind_cf": WIND_CF, "pv_cf": PV_CF, "price_eur_mwh": price}, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


def plant_rate(market_value: float) -> float:
    """The default plant's rate when both technologies have this market value, EUR/MWh."""
    return (WIND_SHARE * max(0.0, DEFAULT.anzulegender_wert_wind - market_value)
            + (1 - WIND_SHARE) * max(0.0, DEFAULT.anzulegender_wert_pv - market_value))


def recording_ladder(calls: list):
    """Stand-in for `run_ladder` that records the premiums it is handed and solves nothing."""
    def ladder(wind, pv, price, run, premium_rate, rebap=None, **kwargs):
        n = len(price)
        b3 = kwargs.get("b3_premium_rate")
        calls.append({"settle": np.broadcast_to(np.asarray(premium_rate, dtype=float), (n,)),
                      "b3": None if b3 is None else np.asarray(b3, dtype=float)})
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

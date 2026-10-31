"""B3 rolling-horizon MPC for the hybrid plant, and the offline parts of the data layer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hybrid.baselines import ladder_tolerance, make_forecasts, run_ladder
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.data import describe, to_plant
from hybrid.market import (effective_price, monthly_market_value, premium_eligible,
                           rebap_series, redispatch_series)


def synth(n: int = 288, seed: int = 0):
    rng = np.random.default_rng(seed)
    h = np.arange(n) * 0.25 % 24
    wind = np.clip(28 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 30 * (pv / 30) \
        + 15 * rng.standard_normal(n)
    return wind, pv, price


@pytest.fixture(scope="module")
def ladder():
    run = RunConfig(dt=0.25, horizon_steps=32, plant=PlantConfig(conn_mw=40.0))
    wind, pv, price = synth()
    return run, run_ladder(wind, pv, price, run, premium_rate=8.0, include_b3=True)


def test_rolling_mpc_respects_the_ceiling(ladder):
    """Checked as run_ladder checks it: revenue before imbalance, within its tolerance."""
    _, res = ladder
    before = {name: r["cost"]["net_revenue"] + r["cost"]["imbalance_cost"]
              for name, r in res.items()}
    b2 = before["B2 perfect foresight"]
    for name, revenue in before.items():
        assert revenue <= b2 + ladder_tolerance(b2), name


def test_rolling_mpc_beats_the_rule_based_baseline(ladder):
    """The invariant whose absence let two B3 defects through.

    B3 once lost to the curtailment-avoidance rule for two reasons: it applied a curtailment
    fraction planned on forecast generation to the true generation, and a linear terminal
    value made it hoard energy. A deployable classical optimum that loses to a heuristic is
    not a baseline worth beating, so this is asserted rather than hoped for.
    """
    _, res = ladder
    assert (res["B3 rolling MPC"]["cost"]["net_revenue"]
            >= res["B1 curtailment avoidance"]["cost"]["net_revenue"])


def test_chosen_curtailment_only_under_negative_prices():
    """With strictly positive prices, any curtailment B3 does must be forced by the connection."""
    run = RunConfig(dt=0.25, horizon_steps=32, plant=PlantConfig(conn_mw=35.0))
    wind, pv, price = synth(192, seed=3)
    price = np.abs(price) + 5.0
    res = run_ladder(wind, pv, price, run, premium_rate=8.0, include_b3=True)
    for name in ("B2 perfect foresight", "B3 rolling MPC"):
        c = res[name]["cost"]
        assert c["forced_curtail_mwh"] > 0, f"{name}: scenario should hit the connection limit"
        assert c["chosen_curtail_mwh"] == pytest.approx(0.0, abs=1e-6), name


def test_rolling_mpc_is_feasible_and_timed(ladder):
    _, res = ladder
    b3 = res["B3 rolling MPC"]
    assert sum(b3["violations"].values()) == 0
    assert len(b3["solve_time_s"]) > 0


def test_b3_plans_its_whole_horizon_on_the_premium_it_knows_now():
    """A plan made late in a month must not price next month's steps at next month's rate.

    That rate is the realised value of the month the plan is made in, which B3 cannot know
    until the month is over. The last four steps here belong to the next month: raising
    their planning rate from 0 to 60 EUR/MWh must not change what B3 does before them. Priced
    step by step instead, the plan charged ahead of them.
    """
    run = RunConfig(dt=0.25, horizon_steps=4, forecast_sigma=0.0,
                    market=MarketConfig(negative_price_rule="none"))
    n = 12
    wind, pv, price = np.full(n, 10.0), np.zeros(n), np.full(n, 50.0)
    before_next_month = {}
    for next_rate in (0.0, 60.0):
        plan = np.r_[np.zeros(8), np.full(4, next_rate)]
        res = run_ladder(wind, pv, price, run, premium_rate=0.0, b3_premium_rate=plan)
        before_next_month[next_rate] = res["B3 rolling MPC"]["p_bat"][:8]
    assert before_next_month[60.0] == pytest.approx(before_next_month[0.0], abs=1e-9)


def test_every_rung_is_settled_on_the_realised_premium():
    """B3 plans on the premium it can know, and is paid the one the law pays."""
    run = RunConfig(dt=0.25, horizon_steps=4, market=MarketConfig(negative_price_rule="none"))
    n = 8
    res = run_ladder(np.full(n, 30.0), np.zeros(n), np.full(n, 50.0), run, premium_rate=12.0,
                     b3_premium_rate=3.0)
    for name, r in res.items():
        assert r["cost"]["premium_revenue"] == pytest.approx(
            np.sum(r["export"]) * 12.0 * run.dt), name


def test_forecasts_do_not_leak_the_truth():
    run = RunConfig()
    wind, pv, price = synth()
    wf, pf, prf = make_forecasts(wind, pv, price, run, np.random.default_rng(0))
    assert np.all(wf >= 0) and np.all(pf >= 0)
    assert not np.allclose(wf, wind) and not np.allclose(prf, price)


def test_six_hour_rule_removes_the_premium_for_the_whole_run():
    """A run of at least six negative hours loses the premium in EVERY one of its steps.

    Regression test. This test used to pin the opposite: the first 23 quarter-hours of a long
    run kept the premium. § 51 (1) EEG 2017 zeroes the applicable value "für den gesamten
    Zeitraum, in dem die Stundenkontrakte ohne Unterbrechung negativ sind" once that period
    reaches six hours, so the whole run loses it, not only its tail.
    """
    hourly = [-1.0] * 7 + [5.0] + [-1.0] * 5 + [5.0]
    price = np.repeat(hourly, 4)                       # hourly prices held on quarter-hours
    e = premium_eligible(price, MarketConfig(negative_price_rule="six_hour"))
    assert not e[:28].any(), "all 28 quarter-hours of a 7 h negative run lose the premium"
    assert e[28:].all(), "a 5 h run keeps it, and the run restarts after a non-negative price"


def test_market_value_is_generation_weighted():
    price = np.array([10.0, 100.0])
    assert monthly_market_value(price, np.array([1.0, 0.0])) == pytest.approx(10.0)
    assert monthly_market_value(price, np.array([1.0, 1.0])) == pytest.approx(55.0)
    assert monthly_market_value(price, np.zeros(2)) == pytest.approx(55.0)


def test_effective_price_includes_fee():
    mk = MarketConfig(negative_price_rule="none", direct_marketing_fee=3.0)
    eff = effective_price(np.array([50.0]), mk, premium_rate=10.0)
    assert eff[0] == pytest.approx(57.0)


def test_redispatch_caps_below_connection():
    plant = PlantConfig()
    cap = redispatch_series(5000, MarketConfig(redispatch_prob_per_step=0.01), plant,
                            np.random.default_rng(0))
    assert cap.max() == pytest.approx(plant.conn_mw)
    assert cap.min() < plant.conn_mw


def test_imbalance_can_be_disabled():
    r = rebap_series(8, np.full(8, 60.0), MarketConfig(imbalance_enabled=False),
                     np.random.default_rng(0))
    assert np.all(r == 0.0)


def test_to_plant_scales_capacity_factors():
    idx = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"wind_cf": [0.0, 0.5, 1.0, 0.25], "pv_cf": [0.0, 0.1, 0.2, 0.0],
                       "price_eur_mwh": [50.0, 60.0, 70.0, 80.0]}, index=idx)
    wind, pv, price = to_plant(df, wind_mw=40.0, pv_mw=10.0)
    assert list(wind) == [0.0, 20.0, 40.0, 10.0]
    assert pv[2] == pytest.approx(2.0)
    assert "over connection" in describe(wind, pv, price, conn_mw=30.0, dt=0.25)

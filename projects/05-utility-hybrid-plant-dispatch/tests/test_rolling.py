"""B3 rolling-horizon MPC for the hybrid plant, and the offline parts of the data layer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hybrid.baselines import make_forecasts, run_ladder
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
    _, res = ladder
    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    for name, r in res.items():
        assert r["cost"]["net_revenue"] <= b2 + 1e-3, name


def test_rolling_mpc_is_feasible_and_timed(ladder):
    _, res = ladder
    b3 = res["B3 rolling MPC"]
    assert sum(b3["violations"].values()) == 0
    assert len(b3["solve_time_s"]) > 0


def test_forecasts_do_not_leak_the_truth():
    run = RunConfig()
    wind, pv, price = synth()
    wf, pf, prf = make_forecasts(wind, pv, price, run, np.random.default_rng(0))
    assert np.all(wf >= 0) and np.all(pf >= 0)
    assert not np.allclose(wf, wind) and not np.allclose(prf, price)


def test_six_hour_rule_counts_consecutive_quarter_hours():
    """Premium is lost only once prices have been negative for 24 consecutive steps."""
    price = np.concatenate([np.full(30, -1.0), [5.0], np.full(10, -1.0)])
    e = premium_eligible(price, MarketConfig(negative_price_rule="six_hour"))
    assert e[:23].all(), "first 23 negative steps still earn the premium"
    assert not e[23:30].any(), "from the 24th consecutive negative step it is suspended"
    assert e[30:].all(), "the run resets after a non-negative price"


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

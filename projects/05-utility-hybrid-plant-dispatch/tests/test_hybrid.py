"""Physics, EEG mechanics and ladder invariants for the hybrid plant."""
from __future__ import annotations

import numpy as np
import pytest

from hybrid.baselines import b0_no_battery, b1_curtailment_avoidance, b2_perfect, run_ladder
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.market import effective_price, premium_eligible, settle
from hybrid.plant import battery_interval, check_feasible, dispatch_step, simulate

RNG = np.random.default_rng(0)


def synth(n: int = 960):
    """Wind + PV + price with realistic co-location structure."""
    h = np.arange(n) * 0.25 % 24
    wind = np.clip(25 + 20 * np.sin(np.arange(n) / 130) + 6 * RNG.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1) * np.clip(
        1 - 0.4 * RNG.random(n), 0, 1)
    price = 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 25 * (pv / 30) \
        + 18 * RNG.standard_normal(n)
    return wind, pv, price


# ------------------------------------------------------------------ physics
def test_export_never_exceeds_connection():
    """The point-of-interconnection limit is hard, whatever the controller asks for."""
    cfg = PlantConfig()
    n = 2000
    wind, pv, _ = synth(n)
    p_bat = RNG.uniform(-3 * cfg.bess_mw, 3 * cfg.bess_mw, n)
    curt = RNG.random(n)
    res = simulate(wind, pv, p_bat, curt, 0.25, cfg)
    assert sum(check_feasible(res, cfg).values()) == 0
    assert res["export"].max() <= cfg.conn_mw + 1e-6


def test_no_grid_charging_when_forbidden():
    """With grid charging disabled the plant must never import."""
    cfg = PlantConfig(grid_charging_allowed=False)
    n = 500
    wind = np.zeros(n)                     # no generation at all
    pv = np.zeros(n)
    p_bat = np.full(n, -cfg.bess_mw)       # ask to charge hard anyway
    res = simulate(wind, pv, p_bat, np.zeros(n), 0.25, cfg)
    assert res["import"].max() < 1e-9
    assert res["p_ch"].max() < 1e-9


def test_battery_interval_respects_soc():
    cfg = PlantConfig()
    lo, hi = battery_interval(cfg.soc_min, 0.25, cfg)
    assert hi == pytest.approx(0.0, abs=1e-9), "empty battery cannot discharge"
    lo, hi = battery_interval(cfg.soc_max, 0.25, cfg)
    assert lo == pytest.approx(0.0, abs=1e-9), "full battery cannot charge"


def test_curtailment_is_split_into_chosen_and_forced():
    """The two kinds settle differently and must be reported separately."""
    cfg = PlantConfig(conn_mw=20.0, bess_mw=0.0, bess_mwh=1e-6)
    r = dispatch_step(40.0, 20.0, 0.0, 0.0, 0.25, 0.25, cfg)
    assert r["chosen_curtail"] == pytest.approx(15.0)     # 25% of 60 MW
    assert r["forced_curtail"] > 0                         # the rest hits the 20 MW cap
    assert r["export"] == pytest.approx(20.0)


# ------------------------------------------------------------------ EEG mechanics
def test_negative_price_rule_vintages():
    price = np.array([50.0, -5.0, -5.0, -5.0, 50.0])
    assert premium_eligible(price, MarketConfig(negative_price_rule="none")).all()
    e = premium_eligible(price, MarketConfig(negative_price_rule="new_2025"))
    assert list(e) == [True, False, False, False, True]


def test_effective_price_is_discontinuous_at_zero():
    """Under the 2025 rule the marginal value of export jumps when the price crosses zero."""
    mk = MarketConfig(negative_price_rule="new_2025", direct_marketing_fee=0.0)
    price = np.array([0.01, -0.01])
    eff = effective_price(price, mk, premium_rate=20.0)
    assert eff[0] == pytest.approx(20.01)
    assert eff[1] == pytest.approx(-0.01)
    assert eff[0] - eff[1] > 15.0


def test_premium_rate_is_exogenous():
    """Regression test for a real bug.

    Deriving the monthly market value from the plant's own export made a controller that
    correctly curtails during negative prices lose its own premium, so perfect foresight
    scored BELOW a do-nothing baseline. The premium is a technology-wide national figure;
    one plant cannot move it, so it must be passed in.
    """
    run = RunConfig(dt=0.25)
    wind, pv, price = synth(400)
    res = b0_no_battery(wind, pv, run)
    a = settle(res, price, run.dt, run.plant, run.market, premium_rate=10.0)
    b = settle(res, price, run.dt, run.plant, run.market, premium_rate=20.0)
    assert b["premium_revenue"] == pytest.approx(2 * a["premium_revenue"])


def test_settlement_decomposition_sums_to_net():
    run = RunConfig(dt=0.25)
    wind, pv, price = synth(400)
    res = b1_curtailment_avoidance(wind, pv, run)
    c = settle(res, price, run.dt, run.plant, run.market, premium_rate=8.0)
    total = (c["spot_revenue"] + c["premium_revenue"] - c["marketing_fee"]
             - c["degradation_cost"] - c["imbalance_cost"])
    assert c["net_revenue"] == pytest.approx(total)


# ------------------------------------------------------------------ ladder
def test_perfect_foresight_beats_every_other_rung():
    """The invariant that caught the premium-feedback bug."""
    run = RunConfig(dt=0.25)
    wind, pv, price = synth(480)
    res = run_ladder(wind, pv, price, run, premium_rate=10.0, include_b3=False)
    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    for name, r in res.items():
        assert r["cost"]["net_revenue"] <= b2 + 1e-3, f"{name} beat perfect foresight"


def test_battery_adds_value_over_no_battery():
    run = RunConfig(dt=0.25, plant=PlantConfig(conn_mw=35.0))
    wind, pv, price = synth(480)
    res = run_ladder(wind, pv, price, run, premium_rate=10.0, include_b3=False)
    assert (res["B2 perfect foresight"]["cost"]["net_revenue"]
            > res["B0 no battery"]["cost"]["net_revenue"])


def test_tighter_connection_forces_more_curtailment():
    run_wide = RunConfig(dt=0.25, plant=PlantConfig(conn_mw=80.0))
    run_tight = RunConfig(dt=0.25, plant=PlantConfig(conn_mw=25.0))
    wind, pv, price = synth(480)
    wide = b0_no_battery(wind, pv, run_wide)
    tight = b0_no_battery(wind, pv, run_tight)
    assert tight["forced_curtail"].sum() > wide["forced_curtail"].sum()

"""Physics, safety layer and ladder invariants for the pumped-storage model."""
from __future__ import annotations

import numpy as np
import pytest

from psw.baselines import b1_price_threshold, b2_perfect, solve_window
from psw.config import MarketConfig, PlantConfig, RunConfig
from psw.market import activation_series, expand_blocks, settle
from psw.plant import (check_feasible, e_next, feasible_interval, security_readiness,
                       simulate)

RNG = np.random.default_rng(0)


def synth_price(n: int = 960) -> np.ndarray:
    """Diurnal price with a solar midday dip and occasional negative hours."""
    h = np.arange(n) * 0.25 % 24
    p = 80 + 35 * np.sin(2 * np.pi * (h - 18) / 24) - 25 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    return p + 15 * RNG.standard_normal(n)


# ------------------------------------------------------------------ safety layer
@pytest.mark.parametrize("dt", [0.25, 1.0])
def test_safety_layer_admits_no_violation(dt):
    """Uniformly random, deliberately absurd setpoints must never breach a constraint."""
    cfg = PlantConfig()
    n = 3000
    proposed = RNG.uniform(-3 * cfg.p_pump_max, 3 * cfg.p_turb_max, n)
    res = simulate(proposed, dt, cfg)
    viol = check_feasible(res, cfg)
    assert sum(viol.values()) == 0, viol


def test_reservoir_restricts_but_never_forces():
    """A full reservoir stops pumping; it must not force turbining (that is what spill is for).

    Regression test for a real bug: the first version made the reservoir bound override the
    ramp limit, so the safety layer leaked up-ramp violations whenever the reservoir filled.
    """
    cfg = PlantConfig()
    lo, hi = feasible_interval(cfg.e_reserve_high, 0.25, cfg, prev_p=0.0)
    assert lo <= 0.0 <= hi, "0 MW must always remain feasible"
    lo, hi = feasible_interval(cfg.e_reserve_low, 0.25, cfg, prev_p=0.0)
    assert lo <= 0.0 <= hi


def test_spill_when_inflow_overtops():
    cfg = PlantConfig(inflow_mw=500.0)          # absurd inflow, to force the case
    e, spill = e_next(cfg.e_reserve_high, 0.0, 0.25, cfg)
    assert e == pytest.approx(cfg.e_reserve_high)
    assert spill > 0.0


def test_ramp_is_per_mode_and_only_upward():
    """Backing off the pumps is a big positive change in NET power but is always allowed."""
    cfg = PlantConfig()
    lo, hi = feasible_interval(cfg.e_init, 0.25, cfg, prev_p=-cfg.p_pump_max)
    assert hi >= 0.0, "must be able to unload the pumps completely in one step"
    # loading the turbine up from zero IS limited
    lo2, hi2 = feasible_interval(cfg.e_init, 0.25, cfg, prev_p=0.0)
    assert hi2 == pytest.approx(cfg.ramp_mw_per_step)


def test_round_trip_efficiency_loses_energy():
    cfg = PlantConfig()
    e0 = 1000.0
    e1, _ = e_next(e0, -100.0, 1.0, PlantConfig(inflow_mw=0.0))
    stored = e1 - e0
    assert stored == pytest.approx(100.0 * cfg.eta_pump)
    assert cfg.eta_round_trip < 1.0


def test_sold_capacity_removes_energy_headroom():
    """Selling aFRR capacity must physically shrink the tradeable range - the core coupling."""
    cfg = PlantConfig()
    _, hi_free = feasible_interval(cfg.e_init, 0.25, cfg, prev_p=cfg.p_turb_max)
    _, hi_sold = feasible_interval(cfg.e_init, 0.25, cfg, prev_p=cfg.p_turb_max,
                                   reserved_pos=100.0)
    assert hi_sold == pytest.approx(hi_free - 100.0)


# ------------------------------------------------------------------ ladder
def test_perfect_foresight_beats_the_rule():
    run = RunConfig(dt=0.25)
    price = synth_price()
    b1 = b1_price_threshold(price, run)
    b2 = b2_perfect(price, run)
    r1 = settle(b1, price, run.dt, run.plant, run.market)["net_revenue"]
    r2 = settle(b2, price, run.dt, run.plant, run.market)["net_revenue"]
    assert r2 >= r1, "perfect foresight cannot lose to a price-threshold rule"


def test_terminal_value_prevents_horizon_end_drain():
    """Defect D4 for a hydro reservoir: without a terminal value the plant empties itself."""
    run = RunConfig(dt=0.25, horizon_steps=96)
    price = synth_price(96)
    e0 = run.plant.e_init
    without = solve_window(price, run.dt, run, e0, terminal_price=0.0)
    with_tv = solve_window(price, run.dt, run, e0, terminal_price=90.0)
    e_without = simulate(without, run.dt, run.plant, e0)["e_res"][-1]
    e_with = simulate(with_tv, run.dt, run.plant, e0)["e_res"][-1]
    assert e_with > e_without, "valuing stored water must leave more of it in the reservoir"


def test_activation_is_bounded_by_sold_capacity():
    cfg = MarketConfig()
    n = 500
    sold_pos = np.full(n, 50.0)
    sold_neg = np.full(n, 40.0)
    act = activation_series(n, sold_pos, sold_neg, cfg, np.random.default_rng(1))
    assert np.all(act <= sold_pos + 1e-9)
    assert np.all(act >= -sold_neg - 1e-9)


def test_settlement_decomposition_sums_to_net():
    run = RunConfig(dt=0.25)
    price = synth_price(200)
    res = b1_price_threshold(price, run)
    n = len(price)
    sp = expand_blocks(np.full(20, 30.0), n, run.market.block_steps)
    sn = expand_blocks(np.full(20, 25.0), n, run.market.block_steps)
    c = settle(res, price, run.dt, run.plant, run.market, sp, sn)
    total = (c["energy_revenue"] - c["pump_cost"] - c["network_cost"]
             + c["capacity_revenue"] + c["activation_revenue"]
             - c["imbalance_cost"] - c["wear_cost"])
    assert c["net_revenue"] == pytest.approx(total)


def test_para_118_6_exemption_matters():
    """The s118(6) EnWG network-charge exemption must change the economics measurably."""
    price = synth_price()
    exempt = RunConfig(dt=0.25, plant=PlantConfig(para_118_6_exempt=True))
    charged = RunConfig(dt=0.25, plant=PlantConfig(para_118_6_exempt=False))
    r_e = settle(b2_perfect(price, exempt), price, 0.25, exempt.plant, exempt.market)
    r_c = settle(b2_perfect(price, charged), price, 0.25, charged.plant, charged.market)
    assert r_e["net_revenue"] > r_c["net_revenue"]
    assert r_c["network_cost"] > 0


def test_security_index_is_bounded():
    run = RunConfig(dt=0.25)
    res = b1_price_threshold(synth_price(400), run)
    s = security_readiness(res, run.plant, run.dt)
    for k, v in s.items():
        assert 0.0 <= v <= 1.0, f"{k} out of range: {v}"

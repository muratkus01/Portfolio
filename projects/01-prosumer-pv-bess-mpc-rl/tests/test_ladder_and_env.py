"""The benchmark ladder and the RL environment, on synthetic data.

These run on every fresh clone. The measured-week regression tests in
`test_legacy_regression.py` skip when the private data is absent; this module is what keeps
the ladder and the environment covered regardless.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from prosumer.baselines.ladder import (b1_rule_based, gap_closure, make_forecasts,
                                       run_ladder)
from prosumer.config import RunConfig, SiteConfig, TariffConfig
from prosumer.market import settlement
from prosumer.model import site


def synthetic_days(days: int = 2, dt: float = 0.25, seed: int = 0):
    """A household with evening demand, midday PV, and a spot price with an evening peak."""
    rng = np.random.default_rng(seed)
    n = int(days * 24 / dt)
    h = np.arange(n) * dt % 24
    load = 0.35 + 0.9 * np.exp(-((h - 19.0) ** 2) / 5) + 0.05 * rng.random(n)
    pv = 4.5 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1) * (0.8 + 0.2 * rng.random(n))
    spot = 0.08 + 0.06 * np.exp(-((h - 19.0) ** 2) / 8) - 0.03 * (pv / 4.5)
    return load, pv, spot


def prices(spot: np.ndarray, run: RunConfig):
    from prosumer.market.tariff import export_price, import_price
    import pandas as pd
    idx = pd.date_range("2024-06-01", periods=len(spot), freq="15min", tz="UTC")
    return import_price(spot, idx, run.tariff), export_price(spot, run.tariff)


# ------------------------------------------------------------------ ladder
@pytest.fixture(scope="module")
def ladder():
    run = RunConfig(dt=0.25, horizon_steps=32)
    load, pv, spot = synthetic_days()
    pi, pe = prices(spot, run)
    return run, load, pv, pi, pe, run_ladder(load, pv, pi, pe, run)


def test_ladder_ordering(ladder):
    """B2 <= B3 in cost: perfect foresight cannot lose to a controller that forecasts."""
    *_, res = ladder
    b2 = res["B2 perfect foresight"]["cost"]["net_cost"]
    b3 = res["B3 rolling MPC"]["cost"]["net_cost"]
    b1 = res["B1 rule-based"]["cost"]["net_cost"]
    assert b2 <= b3 + 1e-6
    assert b2 <= b1 + 1e-6


def test_every_rung_is_feasible(ladder):
    *_, res = ladder
    for name, r in res.items():
        assert sum(r["violations"].values()) == 0, f"{name}: {r['violations']}"
        assert r["balance_err_kw"] < 1e-6, name


def test_b3_records_solve_times(ladder):
    *_, res = ladder
    st = res["B3 rolling MPC"]["solve_time_s"]
    assert len(st) > 0 and np.all(st > 0)


def test_b3_is_not_given_the_truth(ladder):
    """Forecast parity: B3 must plan on forecasts, so with noise it cannot match B2 exactly."""
    *_, res = ladder
    assert res["B3 rolling MPC"]["cost"]["net_cost"] > \
        res["B2 perfect foresight"]["cost"]["net_cost"] + 1e-6


def test_b1_charges_only_from_surplus():
    """The self-consumption rule never charges while the site is a net importer."""
    run = RunConfig(dt=0.25)
    load, pv, _ = synthetic_days()
    res = b1_rule_based(load, pv, run.dt, run)
    deficit = load > pv + 1e-9
    assert np.all(res["p_ch"][deficit] < 1e-9)


def test_forecasts_are_nonnegative_and_noisy():
    run = RunConfig()
    load, pv, _ = synthetic_days()
    lf, pf = make_forecasts(load, pv, run)
    assert lf.shape == load.shape and pf.shape == pv.shape
    assert np.all(lf >= 0) and np.all(pf >= 0)
    assert not np.allclose(lf, load), "a forecast identical to the truth is a leak"


def test_gap_closure_definition():
    assert gap_closure(cost_policy=10.0, cost_b3=10.0, cost_b2=5.0) == pytest.approx(0.0)
    assert gap_closure(cost_policy=5.0, cost_b3=10.0, cost_b2=5.0) == pytest.approx(1.0)
    assert gap_closure(cost_policy=12.0, cost_b3=10.0, cost_b2=5.0) < 0
    assert np.isnan(gap_closure(1.0, 2.0, 2.0))


def test_tariff_spread_is_what_makes_storage_pay():
    """With import priced above export, storing PV must beat exporting it (B2 vs no battery)."""
    run = RunConfig(dt=0.25, tariff=TariffConfig(export_mode="feed_in_tariff"))
    load, pv, spot = synthetic_days()
    pi, pe = prices(spot, run)
    nobat = site.simulate(np.zeros(len(load)), load, pv, run.dt, run.site)
    res = run_ladder(load, pv, pi, pe, run, include_b3=False)
    c_nobat = settlement.net_cost(nobat, pi, pe, run.dt, run.site)
    assert res["B2 perfect foresight"]["cost"]["net_cost"] < c_nobat


# ------------------------------------------------------------------ environment
gym = pytest.importorskip("gymnasium")


def make_env(**kw):
    from prosumer.envs.prosumer_env import ProsumerEnv
    run = RunConfig(dt=0.25)
    load, pv, spot = synthetic_days(days=3)
    pi, pe = prices(spot, run)
    return ProsumerEnv(load, pv, pi, pe, load, pv, run, episode_steps=96, **kw), run


def test_env_passes_gymnasium_checker():
    from gymnasium.utils.env_checker import check_env
    env, _ = make_env()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        check_env(env, skip_render_check=True)


def test_env_random_policy_never_violates():
    """The safety layer's guarantee, exercised through the environment the agent sees."""
    env, run = make_env()
    obs, _ = env.reset(seed=1)
    soc_path = []
    for _ in range(96):
        obs, r, term, trunc, info = env.step(env.action_space.sample() * 3.0)
        soc_path.append(info["soc"])
        assert np.all(np.isfinite(obs))
        if term or trunc:
            break
    soc = np.array(soc_path)
    assert soc.min() >= run.site.soc_min - 1e-9
    assert soc.max() <= run.site.soc_max + 1e-9


def test_env_reward_is_the_settlement_cost():
    """Reward and settlement must be the same number: one cost function, used everywhere."""
    env, _ = make_env(terminal_price=0.0, random_start=False)
    env.reset(seed=0)
    _, r, _, _, info = env.step(np.array([0.0], dtype=np.float32))
    assert r == pytest.approx(-info["cost"] * env.reward_scale)


def test_env_terminal_value_is_paid_once_at_truncation():
    env, _ = make_env(terminal_price=0.25, random_start=False)
    env.reset(seed=0)
    last = None
    for _ in range(96):
        _, r, term, trunc, info = env.step(np.array([0.0], dtype=np.float32))
        last = (r, info, trunc)
        if trunc:
            break
    r, info, trunc = last
    assert trunc
    expected = (-info["cost"] + 0.25 * info["soc"]) * env.reward_scale
    assert r == pytest.approx(expected)


def test_env_respects_episode_starts():
    """Episodes must not straddle a discontinuity in concatenated measured data."""
    env, _ = make_env(episode_starts=np.array([0, 96]))
    for s in range(20):
        env.reset(seed=s)
        assert env.t0 in (0, 96)

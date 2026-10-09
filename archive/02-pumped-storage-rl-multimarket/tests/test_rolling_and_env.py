"""B3 rolling-horizon MPC and the two-timescale RL environment."""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from psw.baselines import b3_rolling, make_price_forecast, run_ladder
from psw.config import MarketConfig, RunConfig
from psw.market import activation_series, block_index, expand_blocks, rebap_series
from psw.plant import check_feasible


def synth_price(n: int = 384, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h = np.arange(n) * 0.25 % 24
    return (80 + 35 * np.sin(2 * np.pi * (h - 18) / 24)
            - 25 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1) + 12 * rng.standard_normal(n))


@pytest.fixture(scope="module")
def ladder():
    run = RunConfig(dt=0.25, horizon_steps=48)
    price = synth_price()
    n = len(price)
    sold = expand_blocks(np.full(int(np.ceil(n / 16)), 30.0), n, 16)
    act = activation_series(n, sold, sold, run.market, np.random.default_rng(1))
    reb = rebap_series(n, price, run.market, np.random.default_rng(2))
    return run, price, run_ladder(price, run, sold, sold, act, reb, include_b3=True)


def test_rolling_mpc_respects_the_ceiling(ladder):
    _, _, res = ladder
    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    assert res["B3 rolling MPC"]["cost"]["net_revenue"] <= b2 + 1e-3


def test_rolling_mpc_beats_the_threshold_rule(ladder):
    _, _, res = ladder
    assert (res["B3 rolling MPC"]["cost"]["net_revenue"]
            > res["B1 price threshold"]["cost"]["net_revenue"])


def test_every_rung_is_feasible(ladder):
    run, _, res = ladder
    for name, r in res.items():
        assert sum(r["violations"].values()) == 0, f"{name}: {r['violations']}"


def test_b3_honours_reserved_headroom():
    """Sold POS capacity must never be consumed by the energy schedule."""
    run = RunConfig(dt=0.25, horizon_steps=32)
    price = synth_price(192)
    n = len(price)
    reserved = np.full(n, 120.0)
    res = b3_rolling(price, make_price_forecast(price, run, np.random.default_rng(0)), run,
                     reserved_pos=reserved, reserved_neg=np.zeros(n), resolve_every=4)
    assert res["p"].max() <= run.plant.p_turb_max - 120.0 + 1e-6
    assert sum(check_feasible(res, run.plant).values()) == 0


def test_block_helpers():
    assert list(block_index(40, 16)[[0, 15, 16, 39]]) == [0, 0, 1, 2]
    e = expand_blocks(np.array([1.0, 2.0, 3.0]), 40, 16)
    assert len(e) == 40 and e[15] == 1.0 and e[16] == 2.0 and e[39] == 3.0


def test_rebap_disabled_is_zero():
    r = rebap_series(10, np.full(10, 50.0), MarketConfig(rebap_enabled=False),
                     np.random.default_rng(0))
    assert np.all(r == 0.0)


# ------------------------------------------------------------------ environment
gym = pytest.importorskip("gymnasium")


def make_env(**kw):
    from psw.env import PSWEnv
    price = synth_price(384)
    return PSWEnv(price, price, RunConfig(dt=0.25), episode_steps=96, **kw)


def test_env_passes_gymnasium_checker():
    from gymnasium.utils.env_checker import check_env
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        check_env(make_env(), skip_render_check=True)


def test_env_random_policy_keeps_the_reservoir_legal():
    env = make_env()
    cfg = env.cfg
    env.reset(seed=3)
    for _ in range(96):
        _, r, term, trunc, info = env.step(env.action_space.sample())
        assert cfg.e_min - 1e-6 <= info["e_res"] <= cfg.e_max + 1e-6
        assert np.isfinite(r)
        if term or trunc:
            break


def test_capacity_is_only_decided_at_block_boundaries():
    """The two-timescale structure: aFRR capacity changes only every 16 steps (4 hours)."""
    env = make_env(random_start=False)
    env.reset(seed=0)
    sold = []
    for k in range(48):
        a = np.array([0.0, 0.8 if k % 2 else 0.1, 0.5], dtype=np.float32)
        _, _, _, _, info = env.step(a)
        sold.append(info["sold_pos"])
    for b in range(3):
        block = sold[b * 16:(b + 1) * 16]
        assert len(set(block)) == 1, "sold capacity changed inside a 4-hour block"


def test_min_bid_size_is_enforced():
    """Below the prequalification minimum, no bid is placed at all."""
    env = make_env(random_start=False)
    env.reset(seed=0)
    tiny = env.mk.afrr_min_bid_mw / env.cfg.p_turb_max * 0.5
    _, _, _, _, info = env.step(np.array([0.0, tiny, tiny], dtype=np.float32))
    assert info["sold_pos"] == 0.0 and info["sold_neg"] == 0.0


def test_lambda_one_rewards_security_not_revenue():
    """At lam = 1 the reward is the security index alone, bounded in [0, 1]."""
    from psw.env import PSWEnv
    price = synth_price(384)
    env = PSWEnv(price, price, RunConfig(dt=0.25, lam=1.0), episode_steps=32,
                 random_start=False)
    env.reset(seed=0)
    for _ in range(32):
        _, r, _, trunc, info = env.step(env.action_space.sample())
        assert r == pytest.approx(info["security"])
        assert 0.0 <= r <= 1.0
        if trunc:
            break

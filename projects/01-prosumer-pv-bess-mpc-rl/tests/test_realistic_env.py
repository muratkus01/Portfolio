"""RealisticProsumerEnv: the observation contains only published prices."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("gymnasium")

from prosumer.envs.realistic_env import RealisticProsumerEnv
from prosumer.scenarios import EXTENSION_RUN as RUN


def _env(pi):
    idx = pd.date_range("2025-06-02", periods=3 * 96, freq="15min",
                        tz="Europe/Berlin").tz_convert("UTC")
    n = len(idx)
    ones = np.ones(n)
    return RealisticProsumerEnv(idx, 0.4 * ones, 0.0 * ones, pi, 0.05 * ones, 0.4 * ones,
                                0.0 * ones, RUN, terminal_values={m: 0.1 for m in range(1, 13)},
                                random_start=False, episode_steps=n,
                                norm_stats={"load": 1.0, "pv": 1.0, "price": 0.5})


def _obs_at(env, local_hhmm):
    loc = env.index.tz_convert("Europe/Berlin")
    t = int(np.flatnonzero((loc.day == 2) & (loc.strftime("%H:%M") == local_hhmm))[0])
    env.reset()
    env.t = t
    return env._obs()


def test_unpublished_prices_do_not_enter_the_observation():
    base = np.full(3 * 96, 0.30)
    changed = base.copy()
    changed[96:192] = 0.90                     # June 3 prices, published June 2 at 13:00
    before_a, before_b = _obs_at(_env(base), "12:45"), _obs_at(_env(changed), "12:45")
    after_a, after_b = _obs_at(_env(base), "13:00"), _obs_at(_env(changed), "13:00")
    np.testing.assert_array_equal(before_a, before_b)
    assert not np.array_equal(after_a, after_b)


def test_calendar_follows_local_clock():
    env = _env(np.full(3 * 96, 0.3))
    o = _obs_at(env, "12:00")
    assert o[4] == pytest.approx(np.sin(2 * np.pi * 48 / 96), abs=1e-6)   # quarter-hour 48


def test_differential_reward_and_action_rescaling():
    env = _env(np.full(3 * 96, 0.35))
    obs, _ = env.reset()
    assert env.reward_mode == "differential"
    assert env.action_mode == "rescale"

    # Step with 0 action (battery idle): differential reward must be 0
    obs, r_idle, _, _, info_idle = env.step(np.array([0.0], dtype=np.float32))
    assert info_idle["p_bat"] == pytest.approx(0.0)
    assert not info_idle["clipped"]
    assert r_idle == pytest.approx(0.0)

    # Step with max positive action (discharge): power is strictly <= hi, clipped is False
    obs, r_dis, _, _, info_dis = env.step(np.array([1.0], dtype=np.float32))
    assert not info_dis["clipped"]
    assert info_dis["p_bat"] > 0.0

    # Step with max negative action (charge): power is strictly >= lo, clipped is False
    obs, r_ch, _, _, info_ch = env.step(np.array([-1.0], dtype=np.float32))
    assert not info_ch["clipped"]
    assert info_ch["p_bat"] < 0.0

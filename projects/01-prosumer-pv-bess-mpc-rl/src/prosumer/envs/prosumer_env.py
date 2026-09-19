"""Gymnasium environment for the prosumer site.

This is the MILP, re-expressed as a Markov decision process. Nothing is re-derived: the
transition is `model.site.step`, the reward is `market.settlement.step_cost`, and the
constraint set becomes `safety.project`. That reuse is the whole point of the conversion -
it is what guarantees the learned policy and the MPC baseline face identical physics and
identical prices.

    MILP element                      ->  MDP element
    ---------------------------------------------------------------
    SoC recursion                     ->  state transition
    charge/discharge mutual exclusion ->  structural (signed action)
    power and SoC bounds              ->  safety layer's feasible interval
    grid import/export limits         ->  safety layer
    objective                         ->  reward (same settlement function)
    perfect-foresight price/PV/load   ->  forecasts in the observation

Reward contains NO constraint penalties. Feasibility is guaranteed by projection, so the
agent cannot trade a violation against profit - the property that separates a deployable
controller from a demo.
"""
from __future__ import annotations

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
    _HAS_GYM = True
except ImportError:                                              # pragma: no cover
    _HAS_GYM = False
    gym = object                                                 # type: ignore

from ..config import RunConfig
from ..market.settlement import step_cost
from ..model.site import soc_next, split_battery_power, wear_energy
from ..safety import feasible_interval

# Horizon windows (in steps) over which forecasts are compressed into the observation.
# Compressing rather than passing 96 raw values keeps the observation small enough to learn
# from, while preserving the structure the decision actually depends on.
LOOKAHEAD_WINDOWS = (4, 12, 48, 96)


class ProsumerEnv(gym.Env if _HAS_GYM else object):              # type: ignore[misc]
    """One site, one continuous action, 15-minute steps.

    Action
        a in [-1, 1]  ->  battery power  p = a * p_inv   (negative charges, positive discharges)
        A signed scalar makes charge/discharge exclusivity structural, replacing the MILP's
        two variables and two binaries.

    Observation
        physical  : SoC (normalised), current PV, load, net position
        calendar  : sin/cos of quarter-hour-of-day and day-of-week
        price     : current import/export price, plus window statistics of the import price
        forecast  : window statistics of forecast PV and load
        regulatory: s14a dimming active flag, steps since the last event

    Reward
        -(step cost in EUR) * reward_scale.  EUR-magnitude rewards destabilise value
        learning, so the scale is explicit rather than left to luck.
    """

    metadata = {"render_modes": []}

    def __init__(self, load: np.ndarray, pv: np.ndarray,
                 price_import: np.ndarray, price_export: np.ndarray,
                 load_fc: np.ndarray, pv_fc: np.ndarray,
                 run: RunConfig, episode_steps: int | None = None,
                 dim_limit: np.ndarray | None = None,
                 terminal_price: float = 0.0,
                 reward_scale: float = 10.0,
                 random_start: bool = True,
                 norm_stats: dict[str, float] | None = None,
                 episode_starts: np.ndarray | None = None):
        if not _HAS_GYM:                                          # pragma: no cover
            raise ImportError("gymnasium is required: pip install '.[rl]'")
        super().__init__()
        self.load, self.pv = np.asarray(load, float), np.asarray(pv, float)
        self.pi, self.pe = np.asarray(price_import, float), np.asarray(price_export, float)
        self.load_fc, self.pv_fc = np.asarray(load_fc, float), np.asarray(pv_fc, float)
        self.run = run
        self.cfg = run.site
        self.dt = run.dt
        self.n = len(self.load)
        self.episode_steps = episode_steps or min(self.n, 7 * run.steps_per_day)
        self.dim = dim_limit
        self.terminal_price = terminal_price
        self.reward_scale = reward_scale
        self.random_start = random_start
        # Permitted episode start indices. When the underlying data is a concatenation of
        # non-contiguous measured periods, an episode must not straddle a discontinuity -
        # the agent would otherwise learn a transition that does not exist.
        self.episode_starts = None if episode_starts is None else np.asarray(episode_starts)

        # Normalisation statistics must come from the TRAINING data only. Passing them in
        # explicitly makes that a visible decision rather than an accident.
        self.stats = norm_stats or {
            "load": max(float(np.percentile(self.load, 99)), 1e-6),
            "pv": max(float(np.percentile(self.pv, 99)), 1e-6),
            "price": max(float(np.percentile(self.pi, 99)), 1e-6),
        }

        self.action_space = spaces.Box(-1.0, 1.0, (1,), np.float32)
        self.observation_space = spaces.Box(-10.0, 10.0, (self._obs_dim(),), np.float32)

        self.t0 = 0
        self.t = 0
        self.soc = self.cfg.soc_init
        self.steps_since_dim = 999

    # ------------------------------------------------------------------ observation
    def _obs_dim(self) -> int:
        return 4 + 4 + 2 + 3 * len(LOOKAHEAD_WINDOWS) + 2

    def _window_stats(self, arr: np.ndarray, scale: float) -> list[float]:
        out = []
        for w in LOOKAHEAD_WINDOWS:
            seg = arr[self.t: min(self.t + w, self.n)]
            out.append(float(seg.mean() / scale) if len(seg) else 0.0)
        return out

    def _obs(self) -> np.ndarray:
        t = min(self.t, self.n - 1)
        soc_n = (self.soc - self.cfg.soc_min) / max(self.cfg.usable_kwh, 1e-9)
        load_n = self.load[t] / self.stats["load"]
        pv_n = self.pv[t] / self.stats["pv"]
        net_n = (self.load[t] - self.pv[t]) / self.stats["load"]

        steps_day = self.run.steps_per_day
        q = t % steps_day
        d = (t // steps_day) % 7
        cal = [np.sin(2 * np.pi * q / steps_day), np.cos(2 * np.pi * q / steps_day),
               np.sin(2 * np.pi * d / 7), np.cos(2 * np.pi * d / 7)]

        price = [self.pi[t] / self.stats["price"], self.pe[t] / self.stats["price"]]

        fc = (self._window_stats(self.pi, self.stats["price"])
              + self._window_stats(self.pv_fc, self.stats["pv"])
              + self._window_stats(self.load_fc, self.stats["load"]))

        dim_active = 0.0 if self.dim is None else float(np.isfinite(self.dim[t]))
        reg = [dim_active, min(self.steps_since_dim, 96) / 96.0]

        return np.array([soc_n, load_n, pv_n, net_n] + cal + price + fc + reg,
                        dtype=np.float32)

    # ------------------------------------------------------------------ gym API
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if not self.random_start:
            self.t0 = 0
        elif self.episode_starts is not None:
            self.t0 = int(self.np_random.choice(self.episode_starts))
        else:
            self.t0 = int(self.np_random.integers(0, max(1, self.n - self.episode_steps)))
        self.t = self.t0
        self.soc = self.cfg.soc_init
        self.steps_since_dim = 999
        return self._obs(), {}

    def step(self, action):
        t = self.t
        proposed = float(np.clip(action[0], -1.0, 1.0)) * self.cfg.p_inv

        d = None if self.dim is None else (
            None if not np.isfinite(self.dim[t]) else float(self.dim[t]))
        lo, hi = feasible_interval(self.soc, float(self.load[t]), float(self.pv[t]),
                                   self.dt, self.cfg, d)
        p = float(np.clip(proposed, lo, hi))

        p_ch, p_dis = split_battery_power(p)
        net = self.load[t] - self.pv[t] - p
        p_imp, p_exp = max(net, 0.0), max(-net, 0.0)
        cost = step_cost(p_imp, p_exp, wear_energy(p_ch, p_dis, self.dt, self.cfg),
                         float(self.pi[t]), float(self.pe[t]), self.dt, self.cfg.c_deg)

        self.soc = soc_next(self.soc, p, self.dt, self.cfg)
        self.steps_since_dim = 0 if d is not None else self.steps_since_dim + 1
        self.t += 1

        truncated = (self.t - self.t0) >= self.episode_steps or self.t >= self.n
        reward = -cost * self.reward_scale
        if truncated:
            # Value the energy left in the battery, so the agent does not learn to dump it
            # at the episode boundary - the RL counterpart of the MPC terminal value (D4).
            reward += self.terminal_price * self.soc * self.reward_scale

        obs = self._obs() if not truncated else np.zeros(self.observation_space.shape,
                                                         dtype=np.float32)
        info = {"p_bat": p, "p_imp": p_imp, "p_exp": p_exp, "soc": self.soc, "cost": cost,
                "clipped": abs(p - proposed) > 1e-9}
        return obs, float(reward), False, bool(truncated), info


def rollout(env: "ProsumerEnv", policy, deterministic: bool = True) -> dict[str, np.ndarray]:
    """Run a policy over one full pass and return the dispatch trajectory."""
    obs, _ = env.reset()
    p_bat, clipped = [], 0
    while True:
        a = policy(obs) if callable(policy) else policy.predict(obs, deterministic=deterministic)[0]
        obs, _r, term, trunc, info = env.step(np.asarray(a).reshape(-1))
        p_bat.append(info["p_bat"])
        clipped += int(info["clipped"])
        if term or trunc:
            break
    return {"p_bat": np.array(p_bat), "clipped_steps": clipped}

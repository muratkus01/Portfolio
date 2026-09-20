"""Gymnasium environment for multi-market pumped-storage dispatch.

The hierarchical decision structure is the interesting part and is modelled explicitly:

    every 16 steps (4 h)  ->  how much aFRR POS / NEG capacity to sell for the next block
    every step (15 min)   ->  net power setpoint, respecting the headroom already sold

Selling capacity is a commitment made before the arbitrage opportunity is known, and its
energy consequence (activation) is stochastic. That is the whole problem, and it is why a
policy that internalises the activation distribution can in principle beat an MPC optimising
against a point forecast.

The operating mode `lam` scalarises revenue against grid-security readiness. Sweeping it
produces the Pareto frontier that is this project's headline deliverable.
"""
from __future__ import annotations

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
    _HAS_GYM = True
except ImportError:                                            # pragma: no cover
    _HAS_GYM = False
    gym = object                                               # type: ignore

from .config import RunConfig
from .market import step_revenue
from .plant import e_next, feasible_interval, security_readiness

LOOKAHEAD = (4, 16, 48, 96, 192)


class PSWEnv(gym.Env if _HAS_GYM else object):                 # type: ignore[misc]
    """Action: [net power fraction, aFRR POS fraction, aFRR NEG fraction] in [-1, 1]^3.

    The capacity components are only READ at a block boundary; in between they are ignored,
    which keeps the action space fixed-size while preserving the two-timescale structure.
    A cleaner formulation uses two separate policies (one per timescale); this single-policy
    version is the groundwork, and the split is the documented next step.
    """

    metadata = {"render_modes": []}

    def __init__(self, price: np.ndarray, price_fc: np.ndarray, run: RunConfig,
                 rebap: np.ndarray | None = None, episode_steps: int | None = None,
                 reward_scale: float = 1e-4, random_start: bool = True,
                 norm_stats: dict[str, float] | None = None, seed: int = 0):
        if not _HAS_GYM:                                       # pragma: no cover
            raise ImportError("gymnasium is required: pip install '.[rl]'")
        super().__init__()
        self.price = np.asarray(price, float)
        self.price_fc = np.asarray(price_fc, float)
        self.rebap = None if rebap is None else np.asarray(rebap, float)
        self.run = run
        self.cfg = run.plant
        self.mk = run.market
        self.dt = run.dt
        self.n = len(self.price)
        self.episode_steps = episode_steps or min(self.n, 14 * run.steps_per_day)
        self.reward_scale = reward_scale
        self.random_start = random_start
        self._rng = np.random.default_rng(seed)

        self.stats = norm_stats or {
            "price": max(float(np.percentile(np.abs(self.price), 95)), 1e-6),
            "p": self.cfg.p_turb_max,
            "e": max(self.cfg.e_max, 1e-6),
        }

        self.action_space = spaces.Box(-1.0, 1.0, (3,), np.float32)
        self.observation_space = spaces.Box(-10.0, 10.0, (7 + len(LOOKAHEAD) + 3,), np.float32)

        self.t = self.t0 = 0
        self.e = self.cfg.e_init
        self.prev_p = 0.0
        self.last_mode = 0
        self.sold_pos = self.sold_neg = 0.0

    # -------------------------------------------------------------- observation
    def _obs(self) -> np.ndarray:
        t = min(self.t, self.n - 1)
        fill = (self.e - self.cfg.e_min) / max(self.cfg.e_max - self.cfg.e_min, 1e-9)
        look = [float(self.price_fc[t:min(t + w, self.n)].mean() / self.stats["price"])
                for w in LOOKAHEAD]
        steps_day = self.run.steps_per_day
        q = t % steps_day
        block_pos = (t % self.mk.block_steps) / self.mk.block_steps
        return np.array(
            [fill,
             self.prev_p / self.stats["p"],
             self.price[t] / self.stats["price"],
             np.sin(2 * np.pi * q / steps_day), np.cos(2 * np.pi * q / steps_day),
             self.sold_pos / self.cfg.p_turb_max,
             self.sold_neg / self.cfg.p_pump_max]
            + look
            + [block_pos, self.run.lam,
               float(self.price_fc[t:t + 96].std() / self.stats["price"]) if t + 2 < self.n else 0.0],
            dtype=np.float32)

    # -------------------------------------------------------------- gym API
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.t0 = (int(self.np_random.integers(0, max(1, self.n - self.episode_steps)))
                   if self.random_start else 0)
        self.t = self.t0
        self.e = self.cfg.e_init
        self.prev_p = 0.0
        self.last_mode = 0
        self.sold_pos = self.sold_neg = 0.0
        return self._obs(), {}

    def step(self, action):
        t = self.t
        a = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)

        # --- capacity decision, only at a block boundary ---
        if t % self.mk.block_steps == 0:
            pos = max(0.0, float(a[1])) * self.cfg.p_turb_max
            neg = max(0.0, float(a[2])) * self.cfg.p_pump_max
            # prequalification minimum bid size: below it, the bid simply is not made
            self.sold_pos = pos if pos >= self.mk.afrr_min_bid_mw else 0.0
            self.sold_neg = neg if neg >= self.mk.afrr_min_bid_mw else 0.0
            if not self.mk.afrr_enabled:
                self.sold_pos = self.sold_neg = 0.0

        # --- energy setpoint, respecting the headroom already committed ---
        proposed = float(a[0]) * (self.cfg.p_turb_max if a[0] >= 0 else self.cfg.p_pump_max)
        lo, hi = feasible_interval(self.e, self.dt, self.cfg, prev_p=self.prev_p,
                                   reserved_pos=self.sold_pos, reserved_neg=self.sold_neg)
        p_commercial = float(np.clip(proposed, lo, hi))

        # --- stochastic activation of what was sold (obligatory once sold) ---
        act = 0.0
        if self.sold_pos > 0 and self._rng.random() < self.mk.afrr_pos_activation_rate * 3:
            act += self.sold_pos * float(self._rng.random())
        if self.sold_neg > 0 and self._rng.random() < self.mk.afrr_neg_activation_rate * 3:
            act -= self.sold_neg * float(self._rng.random())

        lo2, hi2 = feasible_interval(self.e, self.dt, self.cfg, prev_p=self.prev_p)
        p = float(np.clip(p_commercial + act, lo2, hi2))

        cur_mode = 1 if p > 1e-6 else (-1 if p < -1e-6 else 0)
        mode_changed = (cur_mode != 0) and (self.last_mode != 0) and (cur_mode != self.last_mode)
        if cur_mode != 0:
            self.last_mode = cur_mode

        rev = step_revenue(p_commercial, p, float(self.price[t]), self.dt, self.cfg,
                           self.mk, self.sold_pos, self.sold_neg, mode_changed)

        # --- grid-security term, weighted by the operating mode ---
        sec = security_readiness({"p": np.array([p]), "e_res": np.array([self.e])},
                                 self.cfg, self.dt)["index"]
        lam = self.run.lam
        reward = ((1 - lam) * rev * self.reward_scale) + (lam * sec)

        self.e, _spill = e_next(self.e, p, self.dt, self.cfg)
        self.prev_p = p
        self.t += 1
        truncated = (self.t - self.t0) >= self.episode_steps or self.t >= self.n
        obs = self._obs() if not truncated else np.zeros(self.observation_space.shape,
                                                         dtype=np.float32)
        info = {"p": p, "e_res": self.e, "revenue": rev, "security": sec,
                "sold_pos": self.sold_pos, "sold_neg": self.sold_neg, "activation": act}
        return obs, float(reward), False, bool(truncated), info

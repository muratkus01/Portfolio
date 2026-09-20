"""The RL environment on the same information set as the realistic B3.

`ProsumerEnv` is kept unchanged for the legacy four-week experiment. This subclass fixes what
matters once RL is compared with `baselines.rolling.b3_rolling_realistic` on 2025/2026:

  * prices: the observation only contains prices that are published at time t (day D+1 from
    13:00 on day D), exactly the window B3 optimises over. The parent class averaged prices
    over the next 96 steps whether they were known or not.
  * calendar: time of day, weekday and season from the LOCAL clock of the real timestamp.
    The parent derived them from the step count, which is only right for data starting at
    local midnight without daylight saving.
  * price shape: besides window means, the agent sees the minimum, maximum and the rank of
    the current price within the known window, which is what arbitrage decisions depend on.
  * episode end: stored energy is valued with the same monthly terminal values that B3 uses
    (`fit_terminal_values`, fitted on the training year).

Physics, safety layer and reward are inherited, so the agent and the MPC share them exactly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..baselines.rolling import price_known_end
from ..config import RunConfig
from .prosumer_env import ProsumerEnv

LOCAL_TZ = "Europe/Berlin"
PRICE_WINDOWS = (4, 16, 48)
FORECAST_WINDOWS = (4, 16, 48, 96)


class RealisticProsumerEnv(ProsumerEnv):

    def __init__(self, index: pd.DatetimeIndex, load, pv, price_import, price_export,
                 load_fc, pv_fc, run: RunConfig, terminal_values: dict[int, float],
                 price_publication: str = "13:00",
                 reward_mode: str = "differential",
                 action_mode: str = "rescale",
                 **kwargs):
        self.index = index
        self.known_end = price_known_end(index, price_publication)
        local = index.tz_convert(LOCAL_TZ)
        qh = (local.hour * 4 + local.minute // 15).to_numpy()
        doy = local.dayofyear.to_numpy()
        self.calendar = np.column_stack([
            np.sin(2 * np.pi * qh / 96), np.cos(2 * np.pi * qh / 96),
            np.sin(2 * np.pi * local.weekday / 7), np.cos(2 * np.pi * local.weekday / 7),
            np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25),
        ]).astype(np.float32)
        self.month = local.month.to_numpy()
        self.terminal_values = terminal_values
        super().__init__(load, pv, price_import, price_export, load_fc, pv_fc, run,
                         reward_mode=reward_mode, action_mode=action_mode, **kwargs)

    def _obs_dim(self) -> int:
        return 4 + 6 + 2 + len(PRICE_WINDOWS) + 4 + 2 * len(FORECAST_WINDOWS) + 1

    def _mean_window(self, arr, t, w, end, scale):
        seg = arr[t:min(t + w, end)]
        return float(seg.mean() / scale) if len(seg) else 0.0

    def _obs(self) -> np.ndarray:
        t = min(self.t, self.n - 1)
        s = self.stats
        soc_n = (self.soc - self.cfg.soc_min) / max(self.cfg.usable_kwh, 1e-9)
        phys = [soc_n, self.load[t] / s["load"], self.pv[t] / s["pv"],
                (self.load[t] - self.pv[t]) / s["load"]]

        end = int(min(self.known_end[t], self.n))
        known = self.pi[t:end]
        price_now = [self.pi[t] / s["price"], self.pe[t] / s["price"]]
        price_win = [self._mean_window(self.pi, t, w, end, s["price"]) for w in PRICE_WINDOWS]
        rank = float(np.mean(known < self.pi[t])) if len(known) else 0.5
        price_shape = [known.min() / s["price"], known.max() / s["price"], rank,
                       (end - t) / 140.0]

        fc = ([self._mean_window(self.pv_fc, t, w, self.n, s["pv"]) for w in FORECAST_WINDOWS]
              + [self._mean_window(self.load_fc, t, w, self.n, s["load"])
                 for w in FORECAST_WINDOWS])
        tv = [self.terminal_values[int(self.month[t])] / s["price"]]

        obs = np.array(phys + list(self.calendar[t]) + price_now + price_win + price_shape
                       + fc + tv, dtype=np.float32)
        return np.clip(obs, -10.0, 10.0)

    def reset(self, *, seed=None, options=None):
        obs, info = super().reset(seed=seed, options=options)
        last = min(self.t0 + self.episode_steps, self.n) - 1
        self.terminal_price = float(self.terminal_values[int(self.month[last])])
        return self._obs(), info

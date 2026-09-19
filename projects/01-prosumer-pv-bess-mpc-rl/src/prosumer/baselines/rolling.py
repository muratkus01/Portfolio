"""B3 with a realistic information set: what a home energy management system knows, and when.

The first B3 (`ladder.b3_rolling_mpc`) treated day-ahead prices as known over the whole
horizon, so a 24 h window at 10:00 already "knew" prices for 10:00 tomorrow, about three
hours before the auction publishes them. It also drew load and PV forecasts as truth plus
synthetic noise. This module replaces both:

  prices  Day D+1 is known from `price_publication` (default 13:00 local) on day D; EPEX
          publishes at about 12:45. Before that, only day D is known. The optimisation
          window ends where the known prices end, which is what a real system does
          (`max_horizon_steps` can shorten it further). The window is therefore 11 to 35 h.
  PV      The current step is measured. Later steps come from `pv_fc_kw`, the PV model run
          on the weather forecast issued one day earlier (`data.dataset`). Where no
          forecast exists (before 2024-01-20) the value of the same step yesterday is used.
  load    The current step is measured. Later steps come from `load_fc_kw` if present
          (the standard load profile, when the true load is a measured profile), else
          from `load_kw`, i.e. load is known exactly when truth and profile coincide.

"perfect" variants of both forecasts exist to separate the cost of the short, price-limited
horizon from the cost of forecast error.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import RunConfig
from ..model import site
from ..safety import project
from .lp_fast import solve_window_fast
from .milp import default_terminal_price

LOCAL_TZ = "Europe/Berlin"


@dataclass(frozen=True)
class InformationModel:
    price_publication: str = "13:00"        # local time at which day D+1 prices are known
    pv_forecast: str = "nwp"                # "nwp" | "perfect" | "persistence"
    load_forecast: str = "profile"          # "profile" | "perfect" | "persistence"
    max_horizon_steps: int | None = None    # additional cap on the window length

    def __post_init__(self) -> None:
        if self.pv_forecast not in {"nwp", "perfect", "persistence"}:
            raise ValueError(f"unknown pv_forecast {self.pv_forecast!r}")
        if self.load_forecast not in {"profile", "perfect", "persistence"}:
            raise ValueError(f"unknown load_forecast {self.load_forecast!r}")


def price_known_end(index: pd.DatetimeIndex, pub: str = "13:00") -> np.ndarray:
    """For every step, the position (exclusive) up to which prices are known.

    At local time t on day D: known until the end of D, or of D+1 once `pub` has passed.
    """
    local = index.tz_convert(LOCAL_TZ)
    hh, mm = (int(x) for x in pub.split(":"))
    after_pub = (local.hour * 60 + local.minute) >= hh * 60 + mm
    day = local.normalize().tz_localize(None)
    end_day = day + pd.to_timedelta(np.where(after_pub, 2, 1), unit="D")
    end_utc = end_day.tz_localize(LOCAL_TZ).tz_convert("UTC")
    return np.searchsorted(index.to_numpy(), end_utc.to_numpy(), side="left")


def forecast_arrays(df: pd.DataFrame, info: InformationModel, steps_per_day: int
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Full-length arrays of what is forecast for each step (the step itself is observed)."""
    pv, load = df["pv_kw"].to_numpy(), df["load_kw"].to_numpy()
    pv_yday = np.concatenate([pv[:steps_per_day], pv[:-steps_per_day]])
    if info.pv_forecast == "perfect":
        pv_fc = pv.copy()
    elif info.pv_forecast == "persistence":
        pv_fc = pv_yday
    else:
        nwp = df["pv_fc_kw"].to_numpy() if "pv_fc_kw" in df else np.full(len(df), np.nan)
        pv_fc = np.where(np.isnan(nwp), pv_yday, nwp)

    week = 7 * steps_per_day
    if info.load_forecast == "perfect":
        load_fc = load.copy()
    elif info.load_forecast == "persistence":
        load_fc = np.concatenate([load[:week], load[:-week]])
    else:
        load_fc = df["load_fc_kw"].to_numpy() if "load_fc_kw" in df else load.copy()
    return pv_fc, load_fc


def b3_rolling_realistic(df: pd.DataFrame, price_import: np.ndarray, price_export: np.ndarray,
                         run: RunConfig, info: InformationModel = InformationModel(),
                         terminal: str | float = "blend", resolve_every: int = 1,
                         soc0: float | None = None) -> dict[str, np.ndarray]:
    """Rolling-horizon LP-MPC on the information set above.

    terminal: "none" (0), "blend" (import/export blend, see milp.default_terminal_price),
              a number in EUR/kWh, or a dict {month: EUR/kWh} from `fit_terminal_values`,
              looked up by the local month in which the window ends.
    """
    dt, cfg = run.dt, run.site
    load, pv = df["load_kw"].to_numpy(), df["pv_kw"].to_numpy()
    n = len(df)
    known_end = price_known_end(df.index, info.price_publication)
    pv_fc, load_fc = forecast_arrays(df, info, run.steps_per_day)

    s = cfg.soc_init if soc0 is None else soc0
    p_applied = np.zeros(n)
    horizon = np.zeros(n, dtype=int)
    solve_times: list[float] = []
    plan, plan_t = np.zeros(1), 0

    for t in range(n):
        if (t - plan_t) % resolve_every == 0 or t - plan_t >= len(plan):
            h = int(known_end[t] - t)
            if info.max_horizon_steps:
                h = min(h, info.max_horizon_steps)
            lo = np.concatenate([[load[t]], load_fc[t + 1:t + h]])
            pw = np.concatenate([[pv[t]], pv_fc[t + 1:t + h]])
            pi, pe = price_import[t:t + h], price_export[t:t + h]
            if terminal == "none":
                tp = 0.0
            elif isinstance(terminal, dict):
                end_local = df.index[min(t + h, n) - 1].tz_convert(LOCAL_TZ)
                tp = float(terminal[end_local.month])
            elif terminal == "blend":
                tp = default_terminal_price(pi, cfg, run.with_(terminal_value=True,
                                                                terminal_price=None),
                                            price_export=pe, net_load=lo - pw)
            else:
                tp = float(terminal)
            t0 = time.perf_counter()
            sol = solve_window_fast(lo, pw, pi, pe, dt, cfg, s, terminal_price=tp)
            solve_times.append(time.perf_counter() - t0)
            plan = sol["p_bat"] if sol is not None else np.zeros(h)
            plan_t = t
        horizon[t] = len(plan) - (t - plan_t)

        p = project(float(plan[t - plan_t]), s, float(load[t]), float(pv[t]), dt, cfg)
        p_applied[t] = p
        s = site.soc_next(s, p, dt, cfg)

    res = site.simulate(p_applied, load, pv, dt, cfg, soc0)
    res["solve_time_s"] = np.array(solve_times)
    res["horizon_steps"] = horizon
    return res


def fit_terminal_values(df_train: pd.DataFrame, price_import: np.ndarray,
                        price_export: np.ndarray, run: RunConfig) -> dict[int, float]:
    """Value of a stored kWh at local midnight, by month, learned from a PAST period.

    The rolling window always ends at local midnight (where known prices end), so the
    terminal value only needs to be right there. It is taken as the median shadow price of
    the SoC balance at local midnight in a perfect-foresight LP over the training period:
    what an extra stored kWh at midnight was actually worth, given everything that followed.
    Fitting on a past year and applying it to later years keeps the controller honest; no
    information from the evaluation period is used.
    """
    b2 = solve_window_fast(df_train["load_kw"].to_numpy(), df_train["pv_kw"].to_numpy(),
                           price_import, price_export, run.dt, run.site, run.site.soc_init)
    if b2 is None:
        raise RuntimeError("perfect-foresight LP on the training period failed")
    local = df_train.index.tz_convert(LOCAL_TZ)
    # the dual at step t prices SoC at the END of step t; the step ending at midnight is 23:45
    at_midnight = (local.hour == 23) & (local.minute == 45)
    values = pd.Series(b2["soc_value"][at_midnight], index=local[at_midnight].month)
    fitted = values.groupby(level=0).median().clip(lower=0.0)
    return {m: float(fitted.get(m, fitted.median())) for m in range(1, 13)}

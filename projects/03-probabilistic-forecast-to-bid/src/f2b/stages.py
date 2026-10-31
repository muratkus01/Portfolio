"""Decision stages: when each position is fixed, and which forecast is known at that moment.

A balance responsible party decides every delivery quarter-hour several times: once at the
day-ahead gate closure (12:00 Europe/Berlin on the day before delivery) and again at each
intraday stage. A policy may only use the forecast that existed at its decision time.

This module is the single place that turns timestamps into decision times. Policies receive
finished `Stage` objects and never choose an issue time themselves, so a policy cannot read
a forecast from after its own decision. `audit_stages` checks that property and returns the
number of violations, so it can fail.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TZ_MARKET = "Europe/Berlin"


@dataclass(frozen=True)
class Stage:
    """Forecasts for every delivery step as known at one decision stage.

    `issue[t]`     step whose information the forecast for step t uses
    `decision[t]`  step at which the position for step t is fixed; issue <= decision
    `mean[t]`      point forecast (the median for learned forecasts), MW
    `quantiles[t]` predictive quantiles at `levels`, ascending, MW
    """
    name: str
    issue: np.ndarray
    decision: np.ndarray
    mean: np.ndarray
    quantiles: np.ndarray
    levels: np.ndarray

    def quantile(self, tau: float) -> np.ndarray:
        """The tau-quantile for every step, interpolated between the stored levels."""
        if not self.levels[0] <= tau <= self.levels[-1]:
            raise ValueError(f"tau {tau} outside the stored levels "
                             f"{self.levels[0]:g}..{self.levels[-1]:g}")
        j = min(int(np.searchsorted(self.levels, tau, side="right")) - 1, len(self.levels) - 2)
        w = (tau - self.levels[j]) / (self.levels[j + 1] - self.levels[j])
        return (1 - w) * self.quantiles[:, j] + w * self.quantiles[:, j + 1]

    def spread(self) -> np.ndarray:
        """Width between the outermost stored quantiles, the observable uncertainty signal."""
        return self.quantiles[:, -1] - self.quantiles[:, 0]


def gate_closure_steps(index: pd.DatetimeIndex, gate_hour: float = 12.0) -> np.ndarray:
    """Step of the day-ahead gate closure for every delivery step, -1 if it precedes the data.

    The auction for delivery day D closes at `gate_hour` local time on D-1. The returned
    step is the one that starts at the gate closure, so its information set is everything
    known at that instant. Steps whose gate lies before the first index entry get -1: they
    have no day-ahead decision inside the data and must be left out of every evaluation.
    """
    return _steps_at(index, _before_delivery_day(index, 24.0 - gate_hour))


def intraday_decision_steps(index: pd.DatetimeIndex, lead_h: float, dt: float,
                            open_hour: float = 16.0) -> np.ndarray:
    """Decision step of the intraday stage `lead_h` hours before delivery.

    Continuous intraday trading for the quarter-hours of day D opens at `open_hour` local time
    on D-1, so a stage that would fall earlier is moved to the opening. -1 marks steps whose
    decision precedes the data.
    """
    lead = round(lead_h / dt)
    opening = _steps_at(index, _before_delivery_day(index, 24.0 - open_hour))
    dec = np.maximum(np.arange(len(index)) - lead, opening)
    return np.where(dec < 0, -1, dec)


def valid_steps(stages: list[Stage]) -> np.ndarray:
    """Steps that every stage could decide inside the data."""
    return np.all([s.decision >= 0 for s in stages], axis=0)


def audit_stages(stages: list[Stage]) -> int:
    """Number of forecasts read after their decision time or for a step already delivered.

    Zero is required. Each counted entry would be a look-ahead: a position decided with
    information that did not exist yet.
    """
    bad = 0
    for s in stages:
        t = np.arange(len(s.issue))
        ok = s.decision >= 0
        bad += int(np.sum(ok & ((s.issue > s.decision) | (s.decision > t))))
    return bad


def _before_delivery_day(index: pd.DatetimeIndex, hours: float) -> pd.DatetimeIndex:
    """Local midnight of each step's delivery day, minus `hours` of absolute time.

    Clock changes happen at 02:00 or 03:00 local time, so none falls between 04:00 on D-1 and
    midnight of D. For `hours` up to 20 the result is therefore the same wall-clock time of
    D-1 on every day of the year, including both switch days.
    """
    return index.tz_convert(TZ_MARKET).normalize() - pd.Timedelta(hours=hours)


def _steps_at(index: pd.DatetimeIndex, local_times: pd.DatetimeIndex) -> np.ndarray:
    """Position in `index` of each local time, -1 where it precedes the index."""
    utc = local_times.tz_convert("UTC")
    pos = index.searchsorted(utc, side="left")
    inside = (pos < len(index)) & (utc >= index[0])
    exact = np.zeros(len(utc), dtype=bool)
    exact[inside] = index[pos[inside]] == utc[inside]
    if np.any(inside & ~exact):
        raise ValueError("decision times must fall on the index grid; is the index regular?")
    return np.where(inside, pos, -1)

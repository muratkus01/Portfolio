"""Issue-time-safe features for the learned forecasters, and the chronological split.

A row is one delivery step t of one decision stage, decided at step i. Its features use only:

  * measured output up to step i - 1 - `latency_steps` (measurements arrive with a delay),
  * the newest weather vintage already published at i for valid time t (`weather.py`),
  * deterministic calendar and sun geometry of t, and the lead time t - i.

`tests/test_features.py` corrupts everything outside that information set and checks that
every feature stays bit-identical: the leakage check the decision-stage audit cannot do.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .weather import OFFSHORE, VARIABLES, VINTAGES, vintage_for_lead

LATENCY_STEPS = 4                 # measured output is available one hour after the interval
CENTRE = (51.0, 10.0)             # sun geometry is taken at the middle of Germany
WIND_CUT_IN, WIND_RATED = 3.0, 12.0

# Chronological split by TARGET time. Weather vintages start in January/February 2024
# (wind at 100 m from 2024-02-16), hence the training start.
SPLITS = {
    "train": ("2024-02-18", "2025-06-30"),
    "validation": ("2025-07-02", "2025-12-30"),
    "test": ("2026-01-01", "2026-10-31"),
}
EMBARGO_H = 36.0
# The last days of every training window are held out: the learned rungs stop early and
# recalibrate their quantiles there, so neither step ever sees the window they are scored on.
CALIBRATION_DAYS = 60


def build_features(df: pd.DataFrame, weather_q: pd.DataFrame, decision: np.ndarray,
                   dt: float, latency_steps: int = LATENCY_STEPS) -> pd.DataFrame:
    """Features of every step t whose decision step `decision[t]` lies inside the data.

    `df` is the dataset of `data.build_dataset`, `weather_q` the 15-minute weather of
    `weather.to_quarter_hours` on the same index. Rows of undecided steps are dropped; the
    result is indexed by the step's position in `df`.
    """
    n = len(df)
    t = np.arange(n)
    obs = decision - 1 - latency_steps
    rows = np.flatnonzero((decision >= 0) & (obs >= 0))
    t, i, j = rows, decision[rows], obs[rows]
    lead_h = (t - i) * dt
    vintage = vintage_for_lead(lead_h)
    if np.any(vintage > max(VINTAGES)):
        raise ValueError(f"leads up to {lead_h.max():.2f} h need vintage {vintage.max()}, "
                         f"only {max(VINTAGES)} fetched")

    out = {"lead_h": lead_h}
    sites = sorted({c.split("|")[0] for c in weather_q.columns})
    for site in sites:
        for v in VARIABLES:
            out[f"{site}|{v}"] = _vintage_values(weather_q, site, v, t, vintage)
    out.update(_weather_aggregates(out, sites))

    wind_cf = df["wind_cf"].to_numpy(float)
    pv_cf = df["pv_cf"].to_numpy(float)
    out["wind_cf_obs"] = wind_cf[j]
    out["wind_cf_obs_1h"] = np.mean([wind_cf[np.maximum(j - k, 0)] for k in range(4)], axis=0)
    out["pv_cf_obs"] = pv_cf[j]
    # PV seasonal persistence: the same quarter-hour on the latest day already measured.
    # Before the first full day there is none; a negative position would wrap around to the
    # END of the series, i.e. read the future.
    src = t - 96 * np.ceil((t - j) / 96).astype(int)
    out["pv_cf_last_day"] = np.where(src >= 0, pv_cf[np.maximum(src, 0)], np.nan)

    times = df.index[t]
    out.update(_calendar(times))
    out["cos_zenith"] = _cos_zenith(times + pd.Timedelta(minutes=7.5))
    return pd.DataFrame(out, index=pd.Index(t, name="step"))


def split_rows(index: pd.DatetimeIndex, rows: pd.Index, name: str,
               train_end: str | None = None) -> np.ndarray:
    """Boolean mask over `rows` for split `name`, by the TARGET time of each row.

    For "train", `train_end` may move the end (the final fit uses train plus validation),
    and targets within `EMBARGO_H` of the end are dropped.
    """
    start, end = SPLITS[name]
    if name == "train" and train_end is not None:
        end = train_end
    times = index[np.asarray(rows)]
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    if name == "train":
        hi = hi - pd.Timedelta(hours=EMBARGO_H)
    return np.asarray((times >= lo) & (times < hi))


def fit_and_calibration_rows(index: pd.DatetimeIndex, rows: pd.Index,
                             train_end: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The training window split into rows to fit on and the last `CALIBRATION_DAYS` held out.

    An embargo of `EMBARGO_H` separates the two, as it separates training from evaluation.
    """
    train = split_rows(index, rows, "train", train_end)
    times = index[np.asarray(rows)]
    start = times[train].max() - pd.Timedelta(days=CALIBRATION_DAYS)
    calibrate = train & np.asarray(times > start)
    fit = train & np.asarray(times <= start - pd.Timedelta(hours=EMBARGO_H))
    return fit, calibrate


# --------------------------------------------------------------------- helpers
def _vintage_values(weather_q: pd.DataFrame, site: str, var: str, t: np.ndarray,
                    vintage: np.ndarray) -> np.ndarray:
    out = np.full(len(t), np.nan)
    for n in VINTAGES:
        pick = vintage == n
        out[pick] = weather_q[f"{site}|{var}|d{n}"].to_numpy(float)[t[pick]]
    return out


def _power_proxy(ws: np.ndarray) -> np.ndarray:
    """Normalised cubic power curve between cut-in and rated speed, flat above."""
    lo, hi = WIND_CUT_IN ** 3, WIND_RATED ** 3
    return np.clip((np.clip(ws, 0, WIND_RATED) ** 3 - lo) / (hi - lo), 0.0, 1.0)


def _weather_aggregates(feat: dict, sites: list[str]) -> dict:
    onshore = [s for s in sites if s not in OFFSHORE]
    offshore = [s for s in sites if s in OFFSHORE]
    ws_on = np.column_stack([feat[f"{s}|wind_speed_100m"] for s in onshore])
    agg = {
        "wind_on_ws_mean": ws_on.mean(axis=1),
        "wind_on_power_proxy": _power_proxy(ws_on).mean(axis=1),
        "ghi_mean": np.column_stack([feat[f"{s}|shortwave_radiation"] for s in onshore]).mean(1),
        "cloud_mean": np.column_stack([feat[f"{s}|cloud_cover"] for s in onshore]).mean(1),
        "temp_mean": np.column_stack([feat[f"{s}|temperature_2m"] for s in onshore]).mean(1),
    }
    if offshore:
        ws_off = np.column_stack([feat[f"{s}|wind_speed_100m"] for s in offshore])
        agg["wind_off_power_proxy"] = _power_proxy(ws_off).mean(axis=1)
    return agg


def _calendar(times: pd.DatetimeIndex) -> dict:
    hour = times.hour + times.minute / 60.0
    doy = times.day_of_year.to_numpy(float)
    return {"hour_sin": np.sin(2 * np.pi * hour / 24), "hour_cos": np.cos(2 * np.pi * hour / 24),
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25)}


def _cos_zenith(times: pd.DatetimeIndex) -> np.ndarray:
    """Cosine of the solar zenith angle at the centre of Germany (simple NOAA approximation)."""
    lat, lon = np.radians(CENTRE[0]), CENTRE[1]
    doy = times.day_of_year.to_numpy(float)
    hour_utc = times.hour + times.minute / 60.0 + times.second / 3600.0
    gamma = 2 * np.pi / 365.0 * (doy - 1 + (hour_utc - 12) / 24)
    decl = (0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
            - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma)
            - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma))
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma)
                       - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma))
    solar_time = hour_utc * 60 + eqtime + 4 * lon
    ha = np.radians(solar_time / 4 - 180)
    return np.clip(np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha), 0, 1)

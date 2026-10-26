"""Aggregate household load per federal state: a population-mean model fitted to 73 HTW homes.

Why this module exists. The national analysis multiplies one household's load by the number of
home-storage households in a state. Neither obvious input survives that multiplication:

  * BDEW H0 is weather-blind and, apart from the holiday calendar, identical in every state.
    Its nine day shapes and smooth
    day-of-year polynomial do not respond to a cold spell, so load and PV decorrelate on
    exactly the days a storage study cares about.
  * Replaying the measured HTW households keeps their finite-sample noise. Peak-to-mean is
    11.6 for one home and 2.7 for the sum of 74, still falling as 1/sqrt(n); multiplied by
    200,000 homes it describes perfect coincidence, not a fleet.

The model here is the population expectation instead: the expected load of an average
household like the 73 measured in 2010, given the day of week, the state's public holidays,
the season and the day's temperature anomaly. Fleet = N times that.

Model, fitted once on the 73-household mean of 2010 (`experiments.aggregate_load_fit`):
  E(d)   = alpha[class(d)] + harmonics_3(d) . g + [weekend(d)] harmonics_1(d) . h[class(d)]
           + beta * anomaly(d)                                            daily kWh
  w(d,s) = [1, harmonics_2(d)] . G[type(d), s]                           share of the day
  u(t)   = E(d) * w(d, s) / (0.25 * a_ref)                                kW per kWh/a
Seven day classes (Mon..Sat, and Sunday/holiday/24 and 31 Dec), three shape types (working
day, Saturday, Sunday-type). The Saturday and Sunday-type uplift over a working day varies
with the season (in 2010 about 3.0 kWh in Nov-Feb and 1.8 kWh in May-Aug for Sunday-type
days over working days), so those
two classes carry their own first annual harmonic. The anomaly is taken against each site's
own 1991-2020 ERA5 normal, so `annual_kwh` means energy in a normal-weather year and a cold
year comes out above it, as it should. It is clipped to the range seen in 2010, because a
linear slope beyond its support is not defensible.

Temperature scales the day; the intraday shape ignores it. That is a modelling choice with a
measured cost: after the seasonal shape is removed, the hourly shares of 2010 correlate with
the anomaly by at most about 0.2 in absolute value, largest in the evening
(`reports/aggregate_load/shape_temperature.csv`).

What differs between states: only the holiday calendar and the temperature anomaly. That is
deliberate and small; most per-state contrast in residual load comes from fleet size and PV.

Clock. The 2010 file labels behave like legal local time (CET/CEST): the weekday morning ramp
does not move when summer time starts or ends, where a MEZ reading would make it jump an hour.
The fit therefore reads the file on its own grid; this module only ever works on the legal
local clock, with the same 92/100 quarter-hour DST days as `slp.h0_profile`.

Nothing here touches the HTW data. The fitted coefficients are read from
`reports/aggregate_load/`, so a fresh clone can generate per-state load without the download.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .slp import state_holidays

LOCAL_TZ = "Europe/Berlin"
K_ENERGY, K_SHAPE, K_NORMAL = 3, 2, 2      # annual harmonics: daily energy, shape, normal
NORMAL_YEARS = (1991, 2020)
FIT_PROXY = "NI"                            # stand-in site for the unpublished HTW location
N_CLASSES, N_TYPES, N_SLOTS = 7, 3, 96
WEEKEND_CLASSES = (5, 6)                    # Saturday and Sunday-type carry a seasonal uplift
THESIS_ANNUAL_KWH = 3221.0
PROJECT = Path(__file__).resolve().parents[3]
MODEL_DIR = PROJECT / "reports" / "aggregate_load"
# The span of the cached ERA5 download. Extend the end to use later days.
ERA5_SPAN = ("1991-01-01", "2026-10-25")
_NEW_YEARS_EVE_LIKE = {(12, 24), (12, 31)}


@dataclass(frozen=True)
class AggregateLoadModel:
    """Fitted coefficients of the aggregate load model (see the module docstring).

    alpha (7,) kWh/day per day class Mon..Sat, Sunday/holiday; energy_harm (6,) seasonal
    terms of daily energy; weekend_season (4,) first-harmonic cos and sin of the Saturday
    and of the Sunday-type uplift; beta kWh per K per household-day; shape (3, 96, 5) share
    coefficients per type and slot; a_ref normal-weather annual energy of the fitted
    population in kWh; anomaly_range (min, max) in K seen in the fit; proxy the site code.
    """
    alpha: np.ndarray
    energy_harm: np.ndarray
    weekend_season: np.ndarray
    beta: float
    shape: np.ndarray
    a_ref: float
    anomaly_range: tuple[float, float]
    proxy: str


# --------------------------------------------------------------------------- calendar terms
def harmonics(dates, k: int) -> np.ndarray:
    """Annual harmonics [cos t, sin t, ..., cos kt, sin kt], t = 2 pi doy / 365.25.

    dates: dates or timestamps, e.g. [datetime.date(2024, 1, 1)]. Returns shape (n, 2k).
    """
    doy = pd.DatetimeIndex(pd.to_datetime(list(dates))).day_of_year.to_numpy(dtype=float)
    tau = 2.0 * np.pi * doy / 365.25
    cols = []
    for j in range(1, k + 1):
        cols += [np.cos(j * tau), np.sin(j * tau)]
    return np.column_stack(cols) if cols else np.empty((len(doy), 0))


def day_class(dates, holidays: set[_dt.date]) -> np.ndarray:
    """Day class 0..6: Monday 0 to Saturday 5; Sunday, public holiday, 24 and 31 Dec are 6.

    The two December days behave like Sundays in the 2010 data (shape and energy), so they
    deviate on purpose from the BDEW rule that treats them as Saturdays.
    """
    out = []
    for d in pd.to_datetime(list(dates)).date:
        sunday_like = (d.weekday() == 6 or d in holidays
                       or (d.month, d.day) in _NEW_YEARS_EVE_LIKE)
        out.append(6 if sunday_like else d.weekday())
    return np.array(out, dtype=int)


def shape_type(classes: np.ndarray) -> np.ndarray:
    """Shape type per day class: 0 working day, 1 Saturday, 2 Sunday-type."""
    classes = np.asarray(classes)
    return np.where(classes <= 4, 0, np.where(classes == 5, 1, 2))


# --------------------------------------------------------------------------- temperature
def fit_normal(daily_t: pd.Series, years: tuple[int, int] = NORMAL_YEARS,
               k: int = K_NORMAL) -> np.ndarray:
    """Climatological normal of daily mean temperature: [1, harmonics_k] coefficients.

    daily_t: degC on a date index, e.g. an ERA5 series from 1991. Returns 1 + 2k values.
    """
    sel = daily_t[(daily_t.index.year >= years[0]) & (daily_t.index.year <= years[1])]
    if len(sel) < 365 * (years[1] - years[0]):
        raise ValueError(f"normal needs {years[0]}..{years[1]}, got {len(sel)} days")
    x = np.column_stack([np.ones(len(sel)), harmonics(sel.index, k)])
    coefs, *_ = np.linalg.lstsq(x, sel.to_numpy(dtype=float), rcond=None)
    return coefs


def temperature_anomaly(daily_t: pd.Series, normal_coefs: np.ndarray) -> pd.Series:
    """Daily temperature minus the site normal, in K, unclipped, on the same date index."""
    k = (len(normal_coefs) - 1) // 2
    normal = np.column_stack([np.ones(len(daily_t)), harmonics(daily_t.index, k)]) @ normal_coefs
    return pd.Series(daily_t.to_numpy(dtype=float) - normal, index=daily_t.index, name="anomaly")


# --------------------------------------------------------------------------- fit
def energy_design(dates, holidays: set[_dt.date], anomaly: np.ndarray,
                  k: int = K_ENERGY, classes: np.ndarray | None = None) -> np.ndarray:
    """Regressors of daily energy, shape (n, 12 + 2k): 7 class dummies, 2k harmonics, the
    first harmonic for Saturday and for Sunday-type days (4 columns), and the anomaly.

    classes: optional day classes to use instead of the calendar's, e.g. to ask what a
    holiday would have looked like as an ordinary weekday.
    """
    classes = day_class(dates, holidays) if classes is None else np.asarray(classes)
    first = harmonics(dates, 1)
    weekend = [(classes == c)[:, None] * first for c in WEEKEND_CLASSES]
    return np.column_stack([np.eye(N_CLASSES)[classes], harmonics(dates, k), *weekend,
                            np.asarray(anomaly, dtype=float)])


def _split_energy(coefs: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """alpha, seasonal, weekend-seasonal and slope parts of the daily-energy coefficients."""
    harm_end = N_CLASSES + 2 * k
    return coefs[:N_CLASSES], coefs[N_CLASSES:harm_end], coefs[harm_end:-1], float(coefs[-1])


def fit_shape(shares: np.ndarray, dates, types: np.ndarray, k: int = K_SHAPE) -> np.ndarray:
    """Least-squares share coefficients per shape type. shares (n, 96) rows summing to 1.

    Returns (3, 96, 1 + 2k). One lstsq per type with 96 right-hand sides.
    """
    x = np.column_stack([np.ones(len(types)), harmonics(dates, k)])
    out = np.empty((N_TYPES, N_SLOTS, 1 + 2 * k))
    for c in range(N_TYPES):
        m = types == c
        coefs, *_ = np.linalg.lstsq(x[m], shares[m], rcond=None)
        out[c] = coefs.T
    return out


def fit_model(mean_kw: np.ndarray, dates, holidays: set[_dt.date], anomaly: np.ndarray,
              keep: np.ndarray, proxy: str = FIT_PROXY) -> AggregateLoadModel:
    """Fit both equations on the days where `keep` is True.

    mean_kw: (n_days, 96) household-mean load in kW on the legal local clock, for a FULL
    calendar year, because `a_ref` is that year's energy at zero anomaly. dates: the n_days
    local dates. holidays: the fit calendar. anomaly: (n_days,) K at the proxy site.
    """
    energy = mean_kw.sum(axis=1) * 0.25
    x = energy_design(dates, holidays, anomaly)
    coefs, *_ = np.linalg.lstsq(x[keep], energy[keep], rcond=None)
    shares = mean_kw / mean_kw.sum(axis=1, keepdims=True)
    kept_dates = [d for d, k in zip(dates, keep) if k]
    types = shape_type(day_class(kept_dates, holidays))
    shape = fit_shape(shares[keep], kept_dates, types)
    x_normal = energy_design(dates, holidays, np.zeros(len(dates)))
    a_ref = float((x_normal @ coefs).sum())
    a_kept = np.asarray(anomaly, dtype=float)[keep]
    alpha, harm, weekend, beta = _split_energy(coefs, K_ENERGY)
    return AggregateLoadModel(alpha=alpha, energy_harm=harm, weekend_season=weekend, beta=beta,
                              shape=shape, a_ref=a_ref,
                              anomaly_range=(float(a_kept.min()), float(a_kept.max())),
                              proxy=proxy)


def energy_coefs(model: AggregateLoadModel) -> np.ndarray:
    """The daily-energy coefficients in `energy_design` column order."""
    return np.concatenate([model.alpha, model.energy_harm, model.weekend_season, [model.beta]])


# --------------------------------------------------------------------------- predict
def daily_energy(model: AggregateLoadModel, dates, holidays: set[_dt.date],
                 anomaly: np.ndarray) -> tuple[np.ndarray, int]:
    """Expected daily energy in kWh of the average household, and the number of clipped days."""
    raw = np.asarray(anomaly, dtype=float)
    clipped = np.clip(raw, *model.anomaly_range)
    x = energy_design(dates, holidays, clipped, k=len(model.energy_harm) // 2)
    return x @ energy_coefs(model), int((clipped != raw).sum())


def day_shares(model: AggregateLoadModel, dates, holidays: set[_dt.date]) -> np.ndarray:
    """Share of each quarter-hour slot in the day, (n_days, 96), every row summing to 1."""
    types = shape_type(day_class(dates, holidays))
    x = np.column_stack([np.ones(len(types)), harmonics(dates, (model.shape.shape[2] - 1) // 2)])
    w = np.einsum("nk,nsk->ns", x, model.shape[types])
    # Rounding guard only: the fitted rows already sum to 1 within float32 noise.
    w = w / w.sum(axis=1, keepdims=True)
    if (w <= 0).any():
        raise ValueError("non-positive share; the shape model is outside its support")
    return w


def _local_grid(start: str, end: str) -> pd.DatetimeIndex:
    """15-minute UTC index for local dates [start, end), as `slp.h0_profile` builds it."""
    t0 = pd.Timestamp(start).tz_localize(LOCAL_TZ).tz_convert("UTC")
    t1 = pd.Timestamp(end).tz_localize(LOCAL_TZ).tz_convert("UTC")
    return pd.date_range(t0, t1, freq="15min", inclusive="left", name="timestamp")


def unit_load(model: AggregateLoadModel, state: str, start: str, end: str,
              anomaly: pd.Series) -> pd.Series:
    """Load per unit of normal-weather annual energy, kW per kWh/a, 15-minute UTC index.

    state: a code such as "BY". anomaly: K per local date (a date index), covering every day
    in [start, end); `state_anomaly` builds it. DST days keep 92 and 100 quarter-hours and are
    not renormalised, because a 23-hour day uses less energy. attrs: state, clipped_days.
    """
    idx = _local_grid(start, end)
    local = idx.tz_convert(LOCAL_TZ)
    days = pd.unique(local.date)
    holidays = set().union(*(state_holidays(state, y) for y in sorted({d.year for d in days})))
    lookup = pd.Series(anomaly.to_numpy(dtype=float), index=pd.to_datetime(anomaly.index).date)
    missing = [d for d in days if d not in lookup.index]
    if missing:
        raise ValueError(f"no temperature anomaly for {len(missing)} days, first {missing[0]}")
    energy, clipped = daily_energy(model, days, holidays, lookup.loc[list(days)].to_numpy())
    shares = day_shares(model, days, holidays)
    row = pd.Index(days).get_indexer(local.date)
    slot = (local.hour * 4 + local.minute // 15).to_numpy()
    values = energy[row] * shares[row, slot] / (0.25 * model.a_ref)
    out = pd.Series(values, index=idx, name="load_kw_per_kwh")
    out.attrs.update(state=state, clipped_days=clipped)
    return out


def household_load(model: AggregateLoadModel, state: str, start: str, end: str, *,
                   annual_kwh: float, anomaly: pd.Series) -> pd.Series:
    """Average household load in kW for a state, 15-minute UTC index.

    annual_kwh is required: the household's energy in a normal-weather 365-day year, e.g.
    `model.a_ref` (the HTW level) or `THESIS_ANNUAL_KWH`. Realised energy moves with weather
    and calendar and is reported in attrs["realised_kwh"], never rescaled back.
    Example: household_load(m, "BY", "2024-01-01", "2025-01-01", annual_kwh=m.a_ref, anomaly=a)
    """
    unit = unit_load(model, state, start, end, anomaly)
    out = (unit * annual_kwh).rename("load_kw")
    out.attrs.update(unit.attrs, annual_kwh=annual_kwh, realised_kwh=float(out.sum() * 0.25))
    return out


def fleet_load(household_kw: pd.Series, n_households: float | pd.Series) -> pd.Series:
    """Fleet load in MW: household kW times N. A Series of N is forward-filled onto the index.

    n_households: a count such as 237322, or a step Series of counts on timestamps, for
    example `mastr.count_by_state()["BY"]`; its first timestamp must not lie after the start.
    Timestamps without a time zone are read as local dates (the register's monthly
    snapshots), so a step on "2025-02-01" lands at local midnight, not at 01:00.
    """
    if isinstance(n_households, pd.Series):
        counts = n_households.sort_index()
        if counts.index.tz is None:
            counts.index = counts.index.tz_localize(LOCAL_TZ)
        n = counts.reindex(household_kw.index, method="ffill")
        if n.isna().any():
            raise ValueError("household count series starts after the load index")
        n = n.to_numpy(dtype=float)
    else:
        n = float(n_households)
    return (household_kw * n / 1000.0).rename("fleet_mw")


# --------------------------------------------------------------------------- persistence
_SHAPE_TERMS = ["const"] + [f"{f}{j}" for j in range(1, K_SHAPE + 1) for f in ("cos", "sin")]
_ENERGY_TERMS = [f"{f}{j}" for j in range(1, K_ENERGY + 1) for f in ("cos", "sin")]
_WEEKEND_TERMS = ["weekend_cos1", "weekend_sin1"]


def save_model(model: AggregateLoadModel, folder: str | Path, meta: dict | None = None) -> None:
    """Write coefficients.csv (long format) and fit_meta.csv (key, value) to `folder`."""
    rows = [("energy", c, -1, "alpha", v) for c, v in enumerate(model.alpha)]
    rows += [("energy", -1, -1, t, v) for t, v in zip(_ENERGY_TERMS, model.energy_harm)]
    rows += [("energy", c, -1, t, model.weekend_season[2 * i + j])
             for i, c in enumerate(WEEKEND_CLASSES) for j, t in enumerate(_WEEKEND_TERMS)]
    rows.append(("energy", -1, -1, "beta", model.beta))
    for c in range(N_TYPES):
        for s in range(N_SLOTS):
            rows += [("shape", c, s, t, v) for t, v in zip(_SHAPE_TERMS, model.shape[c, s])]
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["block", "day_class", "slot", "term", "value"]).to_csv(
        folder / "coefficients.csv", index=False)
    info = {"a_ref_kwh": model.a_ref, "anomaly_min_k": model.anomaly_range[0],
            "anomaly_max_k": model.anomaly_range[1], "proxy": model.proxy,
            "k_energy": K_ENERGY, "k_shape": K_SHAPE, "k_normal": K_NORMAL,
            "normal_years": f"{NORMAL_YEARS[0]}-{NORMAL_YEARS[1]}", **(meta or {})}
    pd.DataFrame(list(info.items()), columns=["key", "value"]).to_csv(
        folder / "fit_meta.csv", index=False)


def load_model(folder: str | Path = MODEL_DIR) -> AggregateLoadModel:
    """Read the committed model; raises ValueError if a block has the wrong size."""
    folder = Path(folder)
    coef = pd.read_csv(folder / "coefficients.csv")
    meta = pd.read_csv(folder / "fit_meta.csv").set_index("key")["value"]
    energy = coef[coef["block"] == "energy"]
    alpha = energy[energy["term"] == "alpha"].sort_values("day_class")["value"].to_numpy()
    harm = energy.set_index("term").loc[_ENERGY_TERMS, "value"].to_numpy()
    weekend = energy[energy["term"].isin(_WEEKEND_TERMS)].set_index(["day_class", "term"])["value"]
    shape_rows = coef[coef["block"] == "shape"]
    wanted = {(c, t) for c in WEEKEND_CLASSES for t in _WEEKEND_TERMS}
    if (len(alpha) != N_CLASSES or set(weekend.index) != wanted or len(weekend) != len(wanted)
            or len(shape_rows) != N_TYPES * N_SLOTS * len(_SHAPE_TERMS)):
        raise ValueError(f"malformed model in {folder}")
    weekend = np.array([weekend[(c, t)] for c in WEEKEND_CLASSES for t in _WEEKEND_TERMS])
    beta = float(energy.loc[energy["term"] == "beta", "value"].iloc[0])
    shape = (shape_rows.pivot_table(index=["day_class", "slot"], columns="term", values="value")
             [_SHAPE_TERMS].to_numpy().reshape(N_TYPES, N_SLOTS, len(_SHAPE_TERMS)))
    return AggregateLoadModel(alpha=alpha, energy_harm=harm, weekend_season=weekend, beta=beta,
                              shape=shape,
                              a_ref=float(meta["a_ref_kwh"]),
                              anomaly_range=(float(meta["anomaly_min_k"]),
                                             float(meta["anomaly_max_k"])),
                              proxy=str(meta["proxy"]))


def load_state_normals(path: str | Path = MODEL_DIR / "state_normals.csv") -> dict[str, np.ndarray]:
    """Committed 1991-2020 ERA5 normal coefficients per state code, e.g. normals["BY"]."""
    df = pd.read_csv(path).set_index("state")
    return {code: row[[c for c in df.columns if c.startswith("n")]].to_numpy(dtype=float)
            for code, row in df.iterrows()}


def state_anomaly(state: str, start: str, end: str,
                  normals: dict[str, np.ndarray] | None = None) -> pd.Series:
    """Daily temperature anomaly at the state's site for local dates [start, end).

    The only function here that may reach the network: it reads the ERA5 cache under
    data/raw/weather and downloads the full `ERA5_SPAN` once if it is missing.
    """
    from .pv_states import STATE_SITES            # imported here: pv_states pulls in pvlib
    from .weather import fetch_era5_daily_temperature

    if pd.Timestamp(end) > pd.Timestamp(ERA5_SPAN[1]):
        raise ValueError(f"end {end} is after ERA5_SPAN {ERA5_SPAN[1]}; extend ERA5_SPAN")
    site = STATE_SITES[state]
    temp = fetch_era5_daily_temperature(*ERA5_SPAN, (site.latitude, site.longitude),
                                        cache_dir=PROJECT / "data" / "raw" / "weather")
    temp = temp[(temp.index >= pd.Timestamp(start)) & (temp.index < pd.Timestamp(end))]
    normals = load_state_normals() if normals is None else normals
    return temperature_anomaly(temp, normals[state])

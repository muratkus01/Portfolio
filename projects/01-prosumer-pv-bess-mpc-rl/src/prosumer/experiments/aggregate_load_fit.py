"""Fit the aggregate load model to the HTW households, and record the evidence behind it.

Maintainer path. It needs the gitignored HTW 15-minute parquet (CC BY-NC 4.0) and the ERA5
cache or network, and writes the committed model and every validation table to
`reports/aggregate_load/`. Users of the model never need this module: they read the
committed coefficients through `prosumer.data.aggregate_load`.

Three facts about the 2010 file shape the fit, and each is checked rather than assumed
(`aggregate_load_validation`):

  * the labels behave like legal local time, so the file is used on its own grid (row i is
    date i // 96, slot i % 96) and never converted from MEZ;
  * the location is unpublished. The household mean shows no regional holiday, so if the
    homes share one site it is in one of eight northern and eastern states; the test only
    sees a holiday most homes keep (`holiday_detection_power`). Hannover (NI) stands in for
    the site, and the other sites are a sensitivity, not a hidden choice;
  * the two DST days and the four weekdays that were holidays in some states only are left
    out of the fit, so the coefficients do not depend on the location inference.

Nothing derived from the HTW time series is written except regression coefficients and
summary statistics.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from ..data import aggregate_load as al
from ..data.slp import STATES, state_holidays
from . import aggregate_load_validation as val

HTW_FILE_TZ = "Etc/GMT-1"          # how load_htw_15min stored the labels; read back verbatim
DST_DAYS_2010 = {_dt.date(2010, 3, 28), _dt.date(2010, 10, 31)}


def htw_file_grid(profiles: pd.DataFrame, exclude=()) -> tuple[list[_dt.date], np.ndarray]:
    """The 2010 file on its own clock: 365 local dates and a (365, 96, n_households) kW array.

    profiles: the frame of `htw.load_htw_15min` (UTC index built from MEZ labels).
    exclude: household columns to drop, e.g. the PV households flagged in htw_pv_screen.csv.
    """
    labels = profiles.index.tz_convert(HTW_FILE_TZ).tz_localize(None)
    if len(profiles) != 365 * al.N_SLOTS or labels[0] != pd.Timestamp("2010-01-01"):
        raise ValueError("expected one 2010 year of 15-minute rows starting 2010-01-01 00:00")
    data = profiles.drop(columns=list(exclude)).to_numpy(dtype=float)
    dates = list(pd.DatetimeIndex(labels[::al.N_SLOTS]).date)
    return dates, data.reshape(365, al.N_SLOTS, -1)


def fit_calendar_2010() -> tuple[set[_dt.date], set[_dt.date]]:
    """National holidays of 2010 (kept in every state) and the days left out of the fit.

    Left out: the DST days, whose 96 file rows cannot map one to one onto a 23- or 25-hour
    legal day, and the non-Sunday days that were holidays in some states but not all.
    """
    per_state = [state_holidays(code, 2010) for code in STATES]
    national = set.intersection(*per_state)
    regional = set.union(*per_state) - national
    return national, {d for d in regional if d.weekday() != 6} | DST_DAYS_2010


def pv_households(screen_csv: str | Path) -> list[str]:
    """Households flagged as having PV behind the meter (not gross consumption)."""
    screen = pd.read_csv(screen_csv, index_col=0)
    return list(screen.index[screen["pv_suspect"].astype(bool)])


def site_temperatures() -> dict[str, pd.Series]:
    """ERA5 daily mean temperature over `ERA5_SPAN` at the 16 state sites."""
    from ..data.pv_states import STATE_SITES
    from ..data.weather import fetch_era5_daily_temperature

    cache = al.PROJECT / "data" / "raw" / "weather"
    return {code: fetch_era5_daily_temperature(*al.ERA5_SPAN, (s.latitude, s.longitude),
                                               cache_dir=cache)
            for code, s in STATE_SITES.items()}


def state_normals_table(temps: dict[str, pd.Series]) -> pd.DataFrame:
    """1991-2020 normal coefficients per state, the committed `state_normals.csv`."""
    from ..data.pv_states import STATE_SITES

    rows = []
    for code, t in temps.items():
        n = al.fit_normal(t)
        rows.append({"state": code, "latitude": STATE_SITES[code].latitude,
                     "longitude": STATE_SITES[code].longitude,
                     **{f"n{i}": v for i, v in enumerate(n)}})
    return pd.DataFrame(rows)


def anomalies_on(dates, temps: dict[str, pd.Series],
                 normals: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Unclipped anomaly per site on the given dates."""
    want = pd.to_datetime(list(dates))
    return {code: al.temperature_anomaly(t, normals[code]).reindex(want).to_numpy()
            for code, t in temps.items()}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare(htw_path: str | Path, screen_csv: str | Path) -> dict:
    """Everything the fit and the validation share, computed once."""
    profiles = pd.read_parquet(htw_path)
    exclude = pv_households(screen_csv)
    dates, grid = htw_file_grid(profiles, exclude)
    national, excluded = fit_calendar_2010()
    temps = site_temperatures()
    normals = {code: al.fit_normal(t) for code, t in temps.items()}
    return {"dates": dates, "grid": grid, "mean": grid.mean(axis=2), "national": national,
            "keep": np.array([d not in excluded for d in dates]), "excluded": excluded,
            "temps": temps, "normals": normals, "exclude": exclude,
            "anomalies": anomalies_on(dates, temps, normals),
            "sha256": _sha256(Path(htw_path))}


def fit_from(ctx: dict, proxy: str = al.FIT_PROXY) -> al.AggregateLoadModel:
    """The production fit: household mean, fit calendar, anomaly at `proxy`."""
    return al.fit_model(ctx["mean"], ctx["dates"], ctx["national"], ctx["anomalies"][proxy],
                        ctx["keep"], proxy=proxy)


def write_inputs(ctx: dict, out: Path) -> None:
    """Commit-safe inputs: state normals and the 2010 proxy temperature (ERA5, CC BY 4.0)."""
    state_normals_table(ctx["temps"]).to_csv(out / "state_normals.csv", index=False)
    t = ctx["temps"][al.FIT_PROXY]
    t = t[t.index.year == 2010]
    anomaly = al.temperature_anomaly(t, ctx["normals"][al.FIT_PROXY])
    pd.DataFrame({"temp_air": t, "normal": t - anomaly, "anomaly": anomaly}).to_csv(
        out / "fit_temperature_2010.csv", index_label="date")


def run_fit(htw_path: str | Path = "data/processed/htw_74_15min_2010.parquet",
            out_dir: str | Path = "reports/aggregate_load", n_splits: int = 50,
            n_boot: int = 1000, seeds=(0, 1)) -> al.AggregateLoadModel:
    """Fit, save and validate. Writes the model, inputs, validation CSVs and the summary.

    Example: run_fit("data/processed/htw_74_15min_2010.parquet", "reports/aggregate_load")
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ctx = prepare(htw_path, al.PROJECT / "reports" / "htw_pv_screen.csv")
    model = fit_from(ctx)
    al.save_model(model, out, meta={
        "households": ctx["grid"].shape[2], "excluded_households": ";".join(ctx["exclude"]),
        "fit_days": int(ctx["keep"].sum()), "htw_parquet_sha256": ctx["sha256"],
        "temperature": "ERA5 daily mean, Open-Meteo archive", "fit_year": 2010})
    write_inputs(ctx, out)
    summary = val.run_all(ctx, model, out, n_splits=n_splits, n_boot=n_boot, seeds=seeds)
    summary.to_csv(out / "validation_summary.csv", index=False)
    return model

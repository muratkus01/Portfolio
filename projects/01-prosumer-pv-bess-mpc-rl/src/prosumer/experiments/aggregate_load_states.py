"""Per-state aggregate load for 2024 to 2026, and the V10 sanity check on it.

There is no per-state truth to validate against, so this is a sanity check, not a
validation: realised energy must stay within 3 % of the stated annual energy once weather is
allowed for, few days may hit the temperature clip, and load must stay positive. It also
measures how different the states really are, which is the honest answer to the hope that
the aggregate makes per-state shapes "genuinely different": they differ only through
holidays and temperature, and only by a little.

Two energy levels are reported: the HTW population at normal weather (`model.a_ref`) and the
thesis household (3,221 kWh/a). Fleet sizes are the 1 January MaStR snapshot of each year,
labelled as such; the fleet grew by about half during 2024, so a study of a whole year
should use the monthly counts instead. They count lithium-ion home storages, about 3 % below
all chemistries, and take each storage as one household (`n_households_1jan`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..data import aggregate_load as al
from ..data.slp import STATES, h0_profile
from .aggregate_load_validation import row


def periods(last_day: pd.Timestamp, years=(2024, 2025, 2026)) -> list[tuple[int, str, str, bool]]:
    """(year, start, end, partial) per year; a year that reaches past `last_day` is cut there."""
    out = []
    for y in years:
        end = min(pd.Timestamp(f"{y + 1}-01-01"), last_day + pd.Timedelta(days=1))
        out.append((y, f"{y}-01-01", end.date().isoformat(), end < pd.Timestamp(f"{y + 1}-01-01")))
    return out


def _fleet_counts(year: int) -> dict[str, float]:
    from ..data import mastr
    from .national_impact import federal_states

    if not mastr.available():
        return {code: np.nan for code in STATES}
    return {code: float(getattr(s, f"bess_count_{year}")) for code, s in federal_states().items()}


def _daily(series: pd.Series) -> pd.Series:
    return series.groupby(series.index.tz_convert(al.LOCAL_TZ).date).sum() * 0.25


def _state_rows(model, state, year, start, end, partial, anomaly, counts, cross) -> list[dict]:
    unit = al.unit_load(model, state, start, end, anomaly)
    normal = al.unit_load(model, state, start, end, anomaly * 0.0)
    h0 = h0_profile(start, end, annual_kwh=1000.0, holidays=state)
    days = len(_daily(unit))
    rows = []
    for level, annual_kwh in (("htw_normal_weather", model.a_ref), ("thesis_3221", al.THESIS_ANNUAL_KWH)):
        p = unit * annual_kwh
        realised = float(p.sum() * 0.25)
        h0_kw = h0 * realised / (h0.sum() * 0.25)
        n = counts[state]
        rows.append({"state": state, "year": year, "partial": partial, "days": days,
                     "annual_kwh_level": level, "annual_kwh": annual_kwh,
                     "n_households_1jan": n, "realised_kwh_per_household": realised,
                     "ratio_to_annual_x_days": realised / (annual_kwh * days / 365.0),
                     "ratio_to_normal_weather": realised / float(normal.sum() * 0.25 * annual_kwh),
                     "mean_anomaly_k": float(anomaly.mean()), "clipped_days": unit.attrs["clipped_days"],
                     "min_kw_per_household": float(p.min()), "peak_kw_per_household": float(p.max()),
                     "peak_to_mean": float(p.max() / p.mean()), "peak_time_utc": p.idxmax(),
                     "fleet_gwh": realised * n / 1e6, "fleet_peak_mw": float(p.max()) * n / 1000.0,
                     "h0_fleet_peak_mw": float(h0_kw.max()) * n / 1000.0, **cross[state]})
    return rows


def _cross_state(daily_units: pd.DataFrame) -> dict[str, dict]:
    """How far each state's daily energy strays from the 16-state mean, in percent."""
    mean16 = daily_units.mean(axis=1)
    out = {}
    for state in daily_units.columns:
        dev = 100.0 * (daily_units[state] / mean16 - 1.0)
        out[state] = {"corr_daily_with_16_state_mean": float(daily_units[state].corr(mean16)),
                      "sd_daily_dev_pct": float(dev.std()), "max_daily_dev_pct": float(dev.abs().max()),
                      "max_dev_date": dev.abs().idxmax()}
    return out


def state_summary(model: al.AggregateLoadModel, normals: dict[str, np.ndarray],
                  years=(2024, 2025, 2026)) -> pd.DataFrame:
    """One row per state, year and energy level; needs the ERA5 cache (or network)."""
    last_day = min(al.state_anomaly(s, "2024-01-01", al.ERA5_SPAN[1], normals).index[-1]
                   for s in STATES)
    rows = []
    for year, start, end, partial in periods(last_day, years):
        anomalies = {s: al.state_anomaly(s, start, end, normals) for s in STATES}
        daily_units = pd.DataFrame({s: _daily(al.unit_load(model, s, start, end, anomalies[s]))
                                    for s in STATES})
        cross, counts = _cross_state(daily_units), _fleet_counts(year)
        for state in STATES:
            rows += _state_rows(model, state, year, start, end, partial, anomalies[state],
                                counts, cross)
    return pd.DataFrame(rows)


def sanity_rows(summary: pd.DataFrame) -> list:
    """V10 rows of validation_summary.csv, one set per year."""
    out = []
    for year, df in summary.groupby("year"):
        w = float((df["ratio_to_normal_weather"] - 1.0).abs().max())
        out.append(row("V10 states", "max |realised / normal-weather energy - 1|", str(year),
                       w, "<=0.03", w <= 0.03))
        if not df["partial"].iloc[0]:
            d = float((df["ratio_to_annual_x_days"] - 1.0).abs().max())
            out.append(row("V10 states", "max |realised / (annual_kwh x days/365) - 1|", str(year),
                           d, "<=0.03", d <= 0.03))
        c = int(df["clipped_days"].max())
        out.append(row("V10 states", "max clipped days per state", str(year), c, "<=5", c <= 5))
        m = float(df["min_kw_per_household"].min())
        out.append(row("V10 states", "minimum household load kW", str(year), m, ">0", m > 0))
    return out


def run_states(out_dir: str | Path = "reports/aggregate_load") -> pd.DataFrame:
    """Write state_summary.csv and replace the V10 rows in validation_summary.csv."""
    out = Path(out_dir)
    model = al.load_model(out)
    summary = state_summary(model, al.load_state_normals(out / "state_normals.csv"))
    summary.to_csv(out / "state_summary.csv", index=False)
    path = out / "validation_summary.csv"
    previous = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["check"])
    kept = previous[previous["check"] != "V10 states"]
    pd.concat([kept, pd.DataFrame(sanity_rows(summary))]).to_csv(path, index=False)
    return summary

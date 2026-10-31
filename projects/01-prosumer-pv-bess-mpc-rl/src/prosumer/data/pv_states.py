"""Per-state 1 kWp PV generation from Open-Meteo, calibrated against a reference year.

Why this module exists. Scaling household PV up to the federal states needs one PV series per
state, because the states differ in annual yield: a single Munich series applied to all 16
would overstate northern Germany. The per-state targets stay out of the repository (see
below). Nothing uses the PV series yet: `generate()` has no caller, and the national analysis
(`experiments/national_impact.py`) reads only the MaStR fleet counts. Only `STATE_SITES` is
used elsewhere, as the temperature sites of `aggregate_load`.

The method is deliberately split in two, because only one half can live in a public repository:

  * the MODEL is public. Open-Meteo irradiance (CC BY 4.0) through the pvlib chain in `pv.py`,
    one calibration scalar per state.
  * the CALIBRATION TARGETS are not. They come from a company reference export that Murat
    may use locally but not redistribute, so they are read at runtime from a gitignored file
    and are never written into committed code. `calibration_path()` says where. Without that
    file the module still runs on a literature loss of 11 %, and says so: `generate()` and
    `site_system()` issue a `FallbackLossWarning`, and `generate()` records the loss and its
    source in the `attrs` of the series it returns.

Reference-file repair. The 15 state exports carry a defect: their timestamp column is not
monotonic. Exactly one row, always the last, is labelled one year and one hour before the
first, so the underlying array was rolled by one position. Unrepaired, `pd.read_csv` followed
by a sort silently moves one hour of generation from the end of December to the start of
January, and a naive `reindex` against a clean index drops it. `load_reference_1kwp`
relabels the values as a clean hourly range from 1 January and rejects a file with more than
one discontinuity. It does not check the length, the start year or the row order.

Timezone. The state exports label their index UTC and mean it (`+00:00` is explicit in the
file). The Bavarian export is a different vintage: no offset, no daylight-saving transition,
and monotonic. It is read as CET without daylight saving (fixed UTC+1). That is an
assumption: nothing here tells CET from UTC for that file (see `load_reference_1kwp`). The
thesis read UTC stamps as if they were CET, which is the one-hour shift recorded in the
Project 01 README. The thesis itself cannot be changed; everything here is kept on a UTC
index.
"""
from __future__ import annotations

import datetime as dt
import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .pv import PVSystem, pv_power
from .weather import fetch_weather_actual

PROJECT = Path(__file__).resolve().parents[3]

# One point per state: the city coordinates of the state capital, the same points as
# `experiments.national_impact._GEO`. Munich is also the thesis household's site. Altitudes
# are approximate and enter only the solar position (pvlib derives the air pressure from them;
# the Faiman cell-temperature model takes no altitude): 520 m against 0 m changes the
# modelled 2024 Bavarian yield by about 0.001 %.
@dataclass(frozen=True)
class StateSite:
    code: str
    name: str
    latitude: float
    longitude: float
    altitude: float


STATE_SITES: dict[str, StateSite] = {
    s.code: s for s in (
        StateSite("BW", "Baden-Wuerttemberg", 48.7823, 9.1770, 247.0),
        StateSite("BY", "Bavaria", 48.1374, 11.5753, 520.0),
        StateSite("BE", "Berlin", 52.5244, 13.4105, 34.0),
        StateSite("BB", "Brandenburg", 52.3989, 13.0657, 32.0),
        StateSite("HB", "Bremen", 53.0752, 8.8078, 11.0),
        StateSite("HH", "Hamburg", 53.5753, 10.0153, 6.0),
        StateSite("HE", "Hesse", 50.0826, 8.2493, 115.0),
        StateSite("MV", "Mecklenburg-Western Pomerania", 53.6296, 11.4148, 38.0),
        StateSite("NI", "Lower Saxony", 52.3759, 9.7320, 55.0),
        StateSite("NW", "North Rhine-Westphalia", 51.2217, 6.7762, 38.0),
        StateSite("RP", "Rhineland-Palatinate", 49.9842, 8.2791, 89.0),
        StateSite("SL", "Saarland", 49.2333, 7.0000, 230.0),
        StateSite("SN", "Saxony", 51.0509, 13.7383, 113.0),
        StateSite("ST", "Saxony-Anhalt", 52.1277, 11.6292, 48.0),
        StateSite("SH", "Schleswig-Holstein", 54.3213, 10.1394, 20.0),
        StateSite("TH", "Thuringia", 50.9848, 11.0299, 194.0),
    )
}

# Used when no local calibration file is present. Mid-range of the 8 to 14 % band usually
# quoted for lumped losses on a well-installed German rooftop array.
FALLBACK_SYSTEM_LOSS = 0.11


class FallbackLossWarning(UserWarning):
    """A state is modelled with `FALLBACK_SYSTEM_LOSS` because no calibrated loss was found."""


def _warn_fallback(code: str) -> None:
    # A fallback run looks exactly like a calibrated one, so it must never be silent.
    warnings.warn(f"{code}: no calibrated system loss in {calibration_path()}; using the "
                  f"literature fallback of {FALLBACK_SYSTEM_LOSS:.0%}",
                  FallbackLossWarning, stacklevel=3)


def reference_dir() -> Path:
    """Where the company reference exports are expected. Gitignored, never committed."""
    return PROJECT / "data" / "raw" / "thesis_pv"


def calibration_path() -> Path:
    """Where the fitted per-state losses are cached. Gitignored, never committed."""
    return PROJECT / "data" / "processed" / "pv_state_calibration.json"


# --------------------------------------------------------------------------- reference files
def _repair_rolled_index(ts: pd.Series, values: pd.Series, year: int) -> pd.Series:
    """Return an hourly UTC series for `year`, fixing the single rolled row.

    The export is correct in content and wrong in labelling: one row, the last, carries a
    timestamp a year and an hour before the first. This rebuilds the index as a clean hourly
    range from 1 January of `year` and rejects a file with more than one discontinuity. It
    does not check the length, the start year or the order of the rows, so a file with
    another defect can still be relabelled without an error.
    """
    n = len(values)
    expected = pd.date_range(f"{year}-01-01", periods=n, freq="h", tz="UTC")
    bad = int((~ts.sort_values().diff().dropna().eq(pd.Timedelta("1h"))).sum())
    if bad > 1:
        raise ValueError(f"{bad} index discontinuities, expected at most 1; file not understood")
    out = pd.Series(values.to_numpy(dtype=float), index=expected, name="pv_kwh_per_kwp")
    out.index.name = "timestamp"
    return out


def load_reference_1kwp(path: str | Path, year: int = 2024) -> pd.Series:
    """One state's reference 1 kWp hourly generation, kWh per kWp, on a UTC index.

    Handles both export shapes: `MODELLING_ERA5_KWH` with a UTC `START_DATETIME`, and the
    Bavarian `P_PV_1` with an offset-free `DateTime`. That file is read as CET without
    daylight saving (fixed UTC+1) and converted to UTC.
    """
    path = Path(path)
    df = pd.read_csv(path, sep=";")
    if "MODELLING_ERA5_KWH" in df.columns:
        ts = pd.to_datetime(df["START_DATETIME"], utc=True)
        return _repair_rolled_index(ts, df["MODELLING_ERA5_KWH"], year)
    pv_col = next((c for c in df.columns if c.startswith("P_PV")), None)
    if pv_col is None:
        raise ValueError(f"{path.name}: no MODELLING_ERA5_KWH and no P_PV* column")
    ts = pd.to_datetime(df["DateTime"], format="%d.%m.%Y %H:%M")
    # The labels show no daylight-saving transition, so they are not legal local time. That
    # cannot tell CET from UTC, and nothing here tests which one they mean. The thesis PV was
    # one hour early because UTC values sat under CET labels (Project 01 README, Gate 0);
    # whether this file has the same offset is untested.
    local = ts.dt.tz_localize(dt.timezone(dt.timedelta(hours=1)))
    out = pd.Series(df[pv_col].to_numpy(dtype=float),
                    index=pd.DatetimeIndex(local).tz_convert("UTC"), name="pv_kwh_per_kwp")
    out.index.name = "timestamp"
    return out.sort_index()


def find_reference_files(folder: str | Path | None = None) -> dict[str, Path]:
    """Map state code to reference file, matching on the state name in the filename."""
    folder = Path(folder) if folder is not None else reference_dir()
    if not folder.is_dir():
        return {}
    hints = {
        "BW": ("baden",), "BY": ("bavaria", "bayern"), "BE": ("berlin",),
        "BB": ("brandenburg",), "HB": ("bremen",), "HH": ("hamburg",),
        "HE": ("hesse", "hessen"), "MV": ("mecklenburg",), "NI": ("lower saxony", "niedersachsen"),
        "NW": ("north rhine", "nordrhein"), "RP": ("rhineland", "rheinland"),
        "SL": ("saarland",), "SN": ("saxony (", "sachsen)"), "ST": ("saxony-anhalt", "sachsen-anhalt"),
        "SH": ("schleswig",), "TH": ("thuringia", "thuringen", "thüringen"),
    }
    found: dict[str, Path] = {}
    for path in sorted(folder.glob("*.csv")):
        low = path.name.lower()
        # longest hint first, so "saxony-anhalt" wins over "saxony ("
        for code, keys in sorted(hints.items(), key=lambda kv: -max(len(k) for k in kv[1])):
            if code in found:
                continue
            if any(k in low for k in keys):
                found[code] = path
                break
    return found


# --------------------------------------------------------------------------- modelling
def site_system(code: str, kwp: float = 1.0, system_loss: float | None = None) -> PVSystem:
    """The south-facing array at a state's site, `kwp` peak, with the given lumped loss.

    `system_loss=None` means `FALLBACK_SYSTEM_LOSS` and issues a `FallbackLossWarning`.
    """
    site = STATE_SITES[code]
    if system_loss is None:
        _warn_fallback(code)
        system_loss = FALLBACK_SYSTEM_LOSS
    return PVSystem(kwp=kwp, system_loss=system_loss,
                    latitude=site.latitude, longitude=site.longitude, altitude=site.altitude)


def model_1kwp(code: str, start: str, end: str, system_loss: float | None = None) -> pd.Series:
    """Modelled 1 kWp AC generation, kW on a 15-minute UTC index."""
    site = STATE_SITES[code]
    weather = fetch_weather_actual(start, end, location=(site.latitude, site.longitude),
                                   cache_dir=PROJECT / "data" / "raw" / "weather")
    return pv_power(weather, site_system(code, 1.0, system_loss))


def _to_hourly_kwh(power_kw: pd.Series) -> pd.Series:
    """15-minute kW -> hourly kWh, left-labelled, so it lines up with the reference."""
    step_h = (power_kw.index[1] - power_kw.index[0]).total_seconds() / 3600.0
    return (power_kw * step_h).resample("1h").sum()


def fit_system_loss(code: str, reference: pd.Series, year: int = 2024) -> float:
    """Loss that makes the modelled annual yield equal the reference annual yield."""
    lossless = model_1kwp(code, f"{year}-01-01", f"{year + 1}-01-01", system_loss=0.0)
    lossless_yield = float(_to_hourly_kwh(lossless).sum())
    target = float(reference.sum())
    if lossless_yield <= 0:
        raise ValueError(f"{code}: modelled lossless yield is zero")
    return float(np.clip(1.0 - target / lossless_yield, 0.0, 0.5))


def validate(code: str, reference: pd.Series, system_loss: float, year: int = 2024) -> dict:
    """Compare the calibrated model against the reference on shape, not only on yield.

    Annual yield is excluded on purpose: the loss was fitted to it, so agreement there is
    arithmetic rather than evidence. What matters is whether the model reproduces the daily
    pattern, the hourly distribution, the peaks and the time of day of the output.
    """
    modelled = _to_hourly_kwh(model_1kwp(code, f"{year}-01-01", f"{year + 1}-01-01", system_loss))
    both = pd.DataFrame({"model": modelled, "ref": reference}).dropna()
    if both.empty:
        raise ValueError(f"{code}: model and reference do not overlap")
    m, r = both["model"], both["ref"]

    daily_m, daily_r = m.resample("1D").sum(), r.resample("1D").sum()
    day = pd.DataFrame({"m": daily_m, "r": daily_r}).dropna()
    month = pd.DataFrame({"m": m.resample("MS").sum(), "r": r.resample("MS").sum()}).dropna()

    # average hour of generation, as a check on timing. A one-hour error shows here even when
    # the yield matches exactly, which is how the original offset went unnoticed.
    hours = m.index.hour + 0.5
    centroid_m = float((m * hours).sum() / m.sum()) if m.sum() else float("nan")
    centroid_r = float((r * hours).sum() / r.sum()) if r.sum() else float("nan")

    denom = float(r.mean())
    return {
        "state": code,
        "year": year,
        "system_loss": round(system_loss, 5),
        "hours_compared": int(len(both)),
        "yield_model": round(float(m.sum()), 1),
        "yield_reference": round(float(r.sum()), 1),
        "daily_r2": round(float(np.corrcoef(day["m"], day["r"])[0, 1] ** 2), 4),
        "daily_nrmse": round(float(np.sqrt(((day["m"] - day["r"]) ** 2).mean()) / day["r"].mean()), 4),
        "hourly_nrmse": round(float(np.sqrt(((m - r) ** 2).mean()) / denom), 4) if denom else None,
        "hourly_bias_pct": round(float((m.mean() - r.mean()) / denom * 100.0), 3) if denom else None,
        "peak_hour_model": round(float(m.max()), 4),
        "peak_hour_reference": round(float(r.max()), 4),
        "peak_ratio": round(float(m.max() / r.max()), 4) if r.max() else None,
        "worst_month_pct": round(float((month["m"] / month["r"] - 1.0).abs().max() * 100.0), 2),
        "generation_centroid_hour_model": round(centroid_m, 3),
        "generation_centroid_hour_reference": round(centroid_r, 3),
        "centroid_offset_hours": round(centroid_m - centroid_r, 3),
    }


# --------------------------------------------------------------------------- calibration store
def load_calibration() -> dict:
    path = calibration_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def system_loss_for(code: str) -> tuple[float, str]:
    """The loss to use for a state, and where it came from."""
    cal = load_calibration().get("states", {})
    if code in cal:
        return float(cal[code]["system_loss"]), "calibrated"
    return FALLBACK_SYSTEM_LOSS, "fallback"


def generate(code: str, start: str, end: str) -> pd.Series:
    """Per-kWp AC generation for a state over [start, end), 15-minute UTC index, kW.

    The loss is the state's calibrated one when the local calibration file has it, else
    `FALLBACK_SYSTEM_LOSS` with a `FallbackLossWarning`. Either way the series records it:
    `attrs["system_loss"]` and `attrs["loss_source"]` ("calibrated" or "fallback").
    """
    loss, source = system_loss_for(code)
    if source == "fallback":
        _warn_fallback(code)
    out = model_1kwp(code, start, end, system_loss=loss)
    out.attrs.update(system_loss=loss, loss_source=source)
    return out


__all__ = ["STATE_SITES", "StateSite", "FALLBACK_SYSTEM_LOSS", "FallbackLossWarning",
           "reference_dir", "calibration_path", "load_reference_1kwp", "find_reference_files",
           "site_system", "model_1kwp", "fit_system_loss", "validate", "load_calibration",
           "system_loss_for", "generate"]

"""MaStR / battery-charts.de fleet series: monthly German home-storage statistics.

The source files are Grafana CSV exports from battery-charts.de, which is built on the
Marktstammdatenregister. Two properties of that export shape everything here:

  * values carry their unit INSIDE the string ("932 kWh", "5.38 GWh", "6.09 kW"), with a
    three-significant-figure rounding applied before export. A state at 5.38 GWh is therefore
    known to about +/- 5 MWh, which is irrelevant for fleet aggregates and fatal if you try to
    difference two neighbouring months.
  * the time column is `01.Jan2000` style and the LAST row is a partial month (`13.Jan2026`),
    the day the snapshot was taken. Treating it as a full month understates nothing, but
    labelling it "January 2026" and comparing it against a full month elsewhere does.

The series is cumulative installed stock, not monthly additions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[3] / "data" / "raw" / "mastr"

# battery-charts exports English state names, and not the ones anyone else uses.
STATE_CODE: dict[str, str] = {
    "Baden Wurttemberg": "BW",
    "Bavaria": "BY",
    "Berlin": "BE",
    "Brandenburg": "BB",
    "Bremen": "HB",
    "Hamburg": "HH",
    "Hesse": "HE",
    "Mecklenburg--Western-Pommerania": "MV",
    "Lower Saxony": "NI",
    "Northrhine-Westphalia": "NW",
    "Rhineland-Palatine": "RP",
    "Saarland": "SL",
    "Saxony": "SN",
    "Saxony-Anhalt": "ST",
    "Schleswig-Holstein": "SH",
    "Thuringia": "TH",
}

_SCALE = {"wh": 1e-3, "kwh": 1.0, "mwh": 1e3, "gwh": 1e6, "twh": 1e9,
          "w": 1e-3, "kw": 1.0, "mw": 1e3, "gw": 1e6}

# The per-state count exports are one file per state and spell the states differently from the
# combined by-federal-state export, including a typo ("Brandeburg") and a different number of
# hyphens and m's in Mecklenburg. Both spellings are mapped on purpose; correcting the filenames
# instead would break reproduction from a fresh download.
_STATE_FILE_CODE: dict[str, str] = {
    "baden_wurttemberg": "BW",
    "bavaria": "BY",
    "berlin": "BE",
    "brandeburg": "BB",
    "bremen": "HB",
    "hamburg": "HH",
    "hesse": "HE",
    "lower_saxony": "NI",
    "mecklenburg-western-pomerania": "MV",
    "northrhine-westphalia": "NW",
    "rhineland-palatine": "RP",
    "saarland": "SL",
    "saxony": "SN",
    "saxony-anhalt": "ST",
    "schleswig-holstein": "SH",
    "thuringia": "TH",
}

# Every top-level export this module reads. Checking only one of them let a partial download
# pass `available()` and fail later inside `snapshot()` or `mean_size_by_state()`.
_FILES = ("home_storage_mean_energy_power.csv", "battery_count_by_segment.csv",
          "battery_energy_by_segment.csv", "battery_energy_by_state_lithium_ion.csv",
          "battery_power_by_state_lithium_ion.csv")


def available() -> bool:
    """True when the files the national fleet analysis reads are all present.

    The CSVs are gitignored (see SOURCE.md), so a fresh clone and CI have none of them.
    Tests use this to skip instead of failing.
    """
    if not all((RAW / name).exists() for name in _FILES):
        return False
    return all((RAW / "state_counts" / f"{stem}.csv").exists() for stem in _STATE_FILE_CODE)


def parse_quantity(text: str) -> float:
    """`"5.38 GWh"` -> 5_380_000.0 kWh. Returns NaN for blanks.

    Deliberately strict about the unit: an unknown suffix raises instead of silently
    returning the bare number, because a GWh read as a kWh is a factor of a million.
    """
    if text is None:
        return float("nan")
    s = str(text).strip().replace(" ", " ")
    if not s or s.lower() in {"nan", "null", "-"}:
        return float("nan")
    parts = s.split()
    if len(parts) == 1:
        return float(parts[0].replace(",", ""))
    value, unit = parts[0], parts[-1].lower()
    if unit not in _SCALE:
        raise ValueError(f"unknown unit in {text!r}")
    return float(value.replace(",", "")) * _SCALE[unit]


def _parse_time(col: pd.Series) -> pd.DatetimeIndex:
    return pd.to_datetime(col.str.strip(), format="%d.%b%Y")


def _read(name: str) -> pd.DataFrame:
    df = pd.read_csv(RAW / name, skipinitialspace=True)
    df.columns = [c.strip().strip('"') for c in df.columns]
    idx = _parse_time(df["Time"])
    out = df.drop(columns=["Time"]).apply(lambda s: s.map(parse_quantity))
    out.index = idx
    out.index.name = "snapshot"
    return out.sort_index()


def by_state(quantity: str = "energy") -> pd.DataFrame:
    """Installed lithium-ion stock per federal state, monthly. kWh, or kW for power."""
    name = f"battery_{quantity}_by_state_lithium_ion.csv"
    df = _read(name)
    germany = df.pop("Germany") if "Germany" in df else None
    df = df.rename(columns=STATE_CODE)
    unknown = [c for c in df.columns if len(c) != 2]
    if unknown:
        raise ValueError(f"unmapped state columns: {unknown}")
    if germany is not None:
        df["DE"] = germany
    return df


def by_segment(quantity: str = "count") -> pd.DataFrame:
    """Germany-wide stock split into home, industrial and large-scale, monthly."""
    return _read(f"battery_{quantity}_by_segment.csv")


def home_storage_mean() -> pd.DataFrame:
    """Mean energy (kWh) and power (kW) of a single German home storage, monthly."""
    df = _read("home_storage_mean_energy_power.csv")
    return df.rename(columns={"Storage energy": "mean_kwh", "Storage power": "mean_kw"})


def count_by_state(segment: str = "Home storage") -> pd.DataFrame:
    """Number of lithium-ion storages per federal state, monthly, for one segment.

    One CSV per state under `state_counts/`, each split into All, Large-scale, Industrial and
    Home storage. This is the only source in the download that gives a per-state HOME count,
    which is what a per-state mean size needs: the combined by-federal-state energy and power
    files cover all segments together.
    """
    folder = RAW / "state_counts"
    frames: dict[str, pd.Series] = {}
    for path in sorted(folder.glob("*.csv")):
        code = _STATE_FILE_CODE.get(path.stem)
        if code is None:
            raise ValueError(f"unmapped state file {path.name}")
        df = pd.read_csv(path, skipinitialspace=True)
        df.columns = [c.strip().strip('"') for c in df.columns]
        if segment not in df.columns:
            raise KeyError(f"{segment!r} not in {path.name}; has {list(df.columns)}")
        frames[code] = pd.Series(
            df[segment].map(parse_quantity).to_numpy(), index=_parse_time(df["Time"])
        )
    missing = set(_STATE_FILE_CODE.values()) - set(frames)
    if missing:
        raise ValueError(f"missing state files for {sorted(missing)}")
    out = pd.DataFrame(frames).sort_index()
    out.index.name = "snapshot"
    out["DE"] = out.sum(axis=1)
    return out


def mean_size_by_state(date: str = "2024-01-01") -> pd.DataFrame:
    """Mean energy (kWh) and power (kW) of a home storage, per federal state.

    Derived, not published: state energy and power cover ALL lithium-ion segments while the
    count is home only, so the quotient is biased high wherever large-scale batteries sit. The
    bias column makes that visible instead of hiding it, and `home_energy_share` is the
    Germany-wide correction factor for the same effect.

    Treat this as an estimate. The published Germany-wide mean in `home_storage_mean()` is the
    only unbiased figure in the download.
    """
    ts = pd.Timestamp(date)
    energy = by_state("energy").loc[ts]
    power = by_state("power").loc[ts]
    count = count_by_state("Home storage").loc[ts]
    all_count = count_by_state("All storages").loc[ts]

    seg = by_segment("energy").loc[ts]
    home_share = float(seg["Home storage"] / seg["All storages"])

    codes = [c for c in count.index if c != "DE"]
    out = pd.DataFrame({
        "home_count": count[codes],
        "mean_kwh_uncorrected": energy[codes] / count[codes],
        "mean_kw_uncorrected": power[codes] / count[codes],
        "home_count_share_of_all": count[codes] / all_count[codes],
    })
    out["mean_kwh_scaled"] = out["mean_kwh_uncorrected"] * home_share
    out["mean_kw_scaled"] = out["mean_kw_uncorrected"] * home_share
    out.attrs["home_energy_share_de"] = home_share
    out.attrs["snapshot"] = ts
    return out


def snapshot(date: str = "2024-01-01") -> dict[str, float]:
    """The three fleet numbers the national analysis needs, at one snapshot date.

    `date` is matched exactly against the monthly index, so a typo fails loudly rather than
    silently picking a neighbouring month.
    """
    ts = pd.Timestamp(date)
    mean = home_storage_mean()
    count = by_segment("count")
    energy = by_state("energy")
    power = by_state("power")
    for frame, label in ((mean, "home_storage_mean"), (count, "count_by_segment"),
                         (energy, "energy_by_state"), (power, "power_by_state")):
        if ts not in frame.index:
            raise KeyError(f"{ts.date()} not in {label}; available {frame.index.min().date()} "
                           f"to {frame.index.max().date()}")
    return {
        "home_storage_count": float(count.loc[ts, "Home storage"]),
        "mean_kwh": float(mean.loc[ts, "mean_kwh"]),
        "mean_kw": float(mean.loc[ts, "mean_kw"]),
        "li_energy_kwh_de": float(energy.loc[ts, "DE"]),
        "li_power_kw_de": float(power.loc[ts, "DE"]),
    }


def state_shares(date: str = "2024-01-01", quantity: str = "energy") -> pd.Series:
    """Each state's share of the German lithium-ion total at `date`. Sums to 1."""
    df = by_state(quantity)
    ts = pd.Timestamp(date)
    row = df.loc[ts].drop(labels=["DE"])
    return row / row.sum()


def is_partial_month(ts: pd.Timestamp) -> bool:
    """True for the final snapshot row, which is the export date and not a month boundary."""
    return ts.day != 1


def coverage() -> dict[str, object]:
    """What the files actually span. Print this before trusting any year label."""
    mean = home_storage_mean()
    last = mean.index.max()
    return {
        "first": mean.index.min().date().isoformat(),
        "last": last.date().isoformat(),
        "rows": int(len(mean)),
        "last_row_is_partial_month": bool(is_partial_month(last)),
        "full_months_in_2026": int(((mean.index.year == 2026) & (mean.index.day == 1)).sum()),
    }


__all__ = ["by_state", "by_segment", "home_storage_mean", "snapshot", "state_shares",
           "coverage", "parse_quantity", "STATE_CODE", "RAW"]

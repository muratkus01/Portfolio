"""Irradiance and weather from Open-Meteo (CC BY 4.0), cached on disk.

Two products are used:

  * `fetch_weather_actual`   : the historical-forecast archive, 15-minute resolution. Each
                               value is the shortest-lead model output for that time, used
                               as the best available estimate of what actually happened.
  * `fetch_weather_forecast` : the previous-runs archive, i.e. what the weather model
                               predicted `lead_days` days earlier. This is a genuine
                               issue-time forecast, not truth plus synthetic noise.

Open-Meteo radiation values are means over the PRECEDING interval and are labelled with its
end. This package labels intervals by their start (`[t, t + dt)`), so the index is shifted
back by one interval on ingestion.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

MUNICH = (48.1372, 11.5756)
VARIABLES = ("shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
             "temperature_2m", "wind_speed_10m")
_RENAME = {"shortwave_radiation": "ghi", "direct_normal_irradiance": "dni",
           "diffuse_radiation": "dhi", "temperature_2m": "temp_air",
           "wind_speed_10m": "wind_speed"}
ACTUAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _get(url: str, params: dict, retries: int = 3) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=120) as resp:
                return json.load(resp)
        except OSError as exc:
            if attempt == retries - 1:
                raise
            # The archive answers 429 when several long ranges are requested back to back,
            # and a short pause does not clear it.
            time.sleep(60 if getattr(exc, "code", None) == 429 else 5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _yearly_chunks(start: str, end: str):
    """[start, end) split into calendar-year requests (API date ranges are inclusive)."""
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end) - pd.Timedelta(days=1)
    for y in range(t0.year, t1.year + 1):
        a = max(t0, pd.Timestamp(f"{y}-01-01"))
        b = min(t1, pd.Timestamp(f"{y}-12-31"))
        yield a.date().isoformat(), b.date().isoformat()


def _to_frame(block: dict, step: str, suffix: str = "") -> pd.DataFrame:
    df = pd.DataFrame(block)
    idx = pd.to_datetime(df.pop("time"), utc=True) - pd.Timedelta(step)
    df.columns = [c[:-len(suffix)] if suffix and c.endswith(suffix) else c for c in df.columns]
    return df.rename(columns=_RENAME).set_index(pd.DatetimeIndex(idx, name="timestamp"))


def fetch_weather_actual(start: str, end: str, location=MUNICH,
                         cache_dir: str | Path = "data/raw/weather") -> pd.DataFrame:
    """15-minute weather on a UTC index, left-labelled. Columns ghi, dni, dhi (W/m2),
    temp_air (degC), wind_speed (km/h)."""
    cache = Path(cache_dir) / f"actual_15min_{location[0]}_{location[1]}_{start}_{end}.csv"
    if cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    frames = []
    for a, b in _yearly_chunks(start, end):
        data = _get(ACTUAL_URL, {"latitude": location[0], "longitude": location[1],
                                 "timezone": "UTC", "start_date": a, "end_date": b,
                                 "minutely_15": ",".join(VARIABLES)})
        frames.append(_to_frame(data["minutely_15"], "15min"))
    df = pd.concat(frames)
    df = df[~df.index.duplicated()].sort_index()
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    _write_source(cache.parent)
    return df


def fetch_weather_forecast(start: str, end: str, lead_days: int = 1, location=MUNICH,
                           cache_dir: str | Path = "data/raw/weather") -> pd.DataFrame:
    """Hourly weather as forecast `lead_days` earlier, on a UTC index, left-labelled."""
    cache = (Path(cache_dir)
             / f"forecast_d{lead_days}_hourly_{location[0]}_{location[1]}_{start}_{end}.csv")
    if cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    suffix = f"_previous_day{lead_days}"
    frames = []
    for a, b in _yearly_chunks(start, end):
        data = _get(PREVIOUS_RUNS_URL, {
            "latitude": location[0], "longitude": location[1], "timezone": "UTC",
            "start_date": a, "end_date": b,
            "hourly": ",".join(v + suffix for v in VARIABLES)})
        frames.append(_to_frame(data["hourly"], "1h", suffix))
    df = pd.concat(frames)
    df = df[~df.index.duplicated()].sort_index()
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    _write_source(cache.parent)
    return df


def fetch_era5_daily_temperature(start: str, end: str, location: tuple[float, float],
                                 cache_dir: str | Path = "data/raw/weather") -> pd.Series:
    """Daily mean 2 m temperature in degC from ERA5 for local dates [start, end).

    ERA5 serves the fit year, the 1991-2020 normals and the application years alike, because
    an anomaly only means something against a normal from the same product. One request per
    site; daily data is small. Trailing missing days (ERA5 runs about five days behind) are
    dropped, a missing day inside the range raises ValueError.
    Example: fetch_era5_daily_temperature("1991-01-01", "2026-10-25", (52.3759, 9.732)).
    """
    cache = (Path(cache_dir)
             / f"era5_tmean_daily_{location[0]}_{location[1]}_{start}_{end}.csv")
    if cache.exists():
        temp = pd.read_csv(cache, index_col=0, parse_dates=True)["temp_air"]
    else:
        last = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
        data = _get(ARCHIVE_URL, {"latitude": location[0], "longitude": location[1],
                                  "start_date": start, "end_date": last, "models": "era5",
                                  "daily": "temperature_2m_mean", "timezone": "Europe/Berlin"})
        temp = pd.Series(data["daily"]["temperature_2m_mean"], dtype=float, name="temp_air",
                         index=pd.DatetimeIndex(pd.to_datetime(data["daily"]["time"]), name="date"))
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp.to_csv(cache)
        _write_source(cache.parent)
    temp = temp.loc[:temp.last_valid_index()]
    if temp.isna().any():
        raise ValueError(f"missing ERA5 days inside {start}..{end} at {location}: "
                         f"{list(temp.index[temp.isna()].date)[:5]}")
    return temp


def _write_source(folder: Path) -> None:
    (folder / "SOURCE.md").write_text(
        "# Source\n\n- Provider: Open-Meteo (open-meteo.com), licence CC BY 4.0\n"
        f"- Actual weather: {ACTUAL_URL} (minutely_15)\n"
        f"- Issue-time forecasts: {PREVIOUS_RUNS_URL} (hourly, *_previous_dayN)\n"
        "- Index shifted to interval START (Open-Meteo labels the interval end)\n"
        f"- Daily mean temperature: {ARCHIVE_URL} (models=era5, local dates Europe/Berlin)\n")

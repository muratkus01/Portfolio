"""Issue-time weather forecasts from the Open-Meteo previous-runs archive (CC BY 4.0).

`<variable>_previous_dayN` is what the weather model predicted for a valid time N days
(N x 24 h) before it. Only vintages already published at a forecast's issue time may be used,
which `vintage_for_lead` decides. Open-Meteo's historical-forecast archive is NOT used: it
stitches the shortest-lead output of every run, so a feature built from it would leak.

Radiation is a mean over the hour BEFORE its label; the other variables are instantaneous.
`to_quarter_hours` places each at its true time before interpolating to 15-minute intervals.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
VARIABLES = ("wind_speed_100m", "shortwave_radiation", "cloud_cover", "temperature_2m")
RADIATION = ("shortwave_radiation",)
VINTAGES = (1, 2)
PUBLICATION_LATENCY_H = 6.0

# State capitals (the P01 sites) for onshore wind and PV, plus two offshore wind clusters.
SITES: dict[str, tuple[float, float]] = {
    "BW": (48.7823, 9.1770), "BY": (48.1374, 11.5753), "BE": (52.5244, 13.4105),
    "BB": (52.3989, 13.0657), "HB": (53.0752, 8.8078), "HH": (53.5753, 10.0153),
    "HE": (50.0826, 8.2493), "MV": (53.6296, 11.4148), "NI": (52.3759, 9.7320),
    "NW": (51.2217, 6.7762), "RP": (49.9842, 8.2791), "SL": (49.2333, 7.0000),
    "SN": (51.0509, 13.7383), "ST": (52.1277, 11.6292), "SH": (54.3213, 10.1394),
    "TH": (50.9848, 11.0299),
    "OFF_NORTH": (54.3, 6.5), "OFF_BALTIC": (54.7, 13.7),
}
OFFSHORE = ("OFF_NORTH", "OFF_BALTIC")


def vintage_for_lead(lead_h: np.ndarray | float) -> np.ndarray:
    """Newest vintage N that was already published `lead_h` hours before the valid time.

    A run for valid time T made N days earlier is assumed public `PUBLICATION_LATENCY_H`
    after it was made, so it is usable at issue time T - lead when N x 24 >= lead + latency.
    """
    return np.ceil((np.asarray(lead_h, float) + PUBLICATION_LATENCY_H) / 24.0).astype(int)


def fetch_previous_runs(start: str, end: str, cache_dir: Path,
                        sites: dict[str, tuple[float, float]] = SITES,
                        group: int = 6) -> pd.DataFrame:
    """Hourly forecasts for every site, variable and vintage on a UTC index of valid times.

    Columns are named `<site>|<variable>|d<N>`. Requests are made per calendar year and per
    group of `group` sites, and each raw response is cached under `cache_dir`.
    """
    names = list(sites)
    years = []
    for a, b in _yearly_chunks(start, end):
        groups = [_fetch_chunk(names[g:g + group], sites, a, b, cache_dir)
                  for g in range(0, len(names), group)]
        years.append(pd.concat(groups, axis=1))
    out = pd.concat(years).sort_index()
    return out[~out.index.duplicated()]


def to_quarter_hours(hourly: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Hourly values interpolated to the MIDPOINT of each 15-minute interval of `index`.

    Radiation labelled L is the mean over [L - 1 h, L), so it is placed at L - 30 min; the
    instantaneous variables stay at their label.
    """
    t_hour = _seconds(hourly.index)
    t_q = _seconds(index) + 450.0
    cols = {}
    for c in hourly.columns:
        x = t_hour - 1800.0 if c.split("|")[1] in RADIATION else t_hour
        y = hourly[c].to_numpy(float)
        ok = ~np.isnan(y)
        cols[c] = (np.interp(t_q, x[ok], y[ok], left=np.nan, right=np.nan) if ok.sum() > 1
                   else np.full(len(index), np.nan))
    return pd.DataFrame(cols, index=index)


# --------------------------------------------------------------------- plumbing
def _fetch_chunk(chunk: list[str], sites: dict, a: str, b: str, cache_dir: Path) -> pd.DataFrame:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"previous_runs_{'-'.join(chunk)}_{a}_{b}.json"
    if cache.exists():
        payload = json.loads(cache.read_text())
    else:
        hourly = [f"{v}_previous_day{n}" for v in VARIABLES for n in VINTAGES]
        params = {"latitude": ",".join(f"{sites[s][0]}" for s in chunk),
                  "longitude": ",".join(f"{sites[s][1]}" for s in chunk),
                  "hourly": ",".join(hourly), "start_date": a, "end_date": b,
                  "timezone": "UTC", "wind_speed_unit": "ms"}
        url = f"{URL}?{urllib.parse.urlencode(params)}"
        payload = _get(url)
        cache.write_text(json.dumps(payload))
        _write_source(cache_dir, cache.name, url)
    blocks = payload if isinstance(payload, list) else [payload]
    frames = []
    for site, block in zip(chunk, blocks):
        h = block["hourly"]
        idx = pd.DatetimeIndex(pd.to_datetime(h["time"], utc=True), name="timestamp")
        data = {}
        for v in VARIABLES:
            for n in VINTAGES:
                key = f"{v}_previous_day{n}"
                data[f"{site}|{v}|d{n}"] = np.asarray(h[key], dtype=float)
        frames.append(pd.DataFrame(data, index=idx))
    return pd.concat(frames, axis=1)


def _get(url: str, retries: int = 4) -> dict | list:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries - 1:
                raise
            time.sleep(60)       # Open-Meteo's rate limit does not clear with a short pause
    raise RuntimeError("unreachable")


def _yearly_chunks(start: str, end: str):
    """[start, end) as calendar-year requests; the API's date ranges are inclusive."""
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end) - pd.Timedelta(days=1)
    for y in range(t0.year, t1.year + 1):
        a = max(t0, pd.Timestamp(f"{y}-01-01"))
        b = min(t1, pd.Timestamp(f"{y}-12-31"))
        yield a.date().isoformat(), b.date().isoformat()


def _write_source(cache_dir: Path, name: str, url: str) -> None:
    src = cache_dir / "SOURCE.md"
    if not src.exists():
        src.write_text("# Open-Meteo previous-runs archive\n\nCC BY 4.0, Open-Meteo.com. "
                       "Hourly values per site; radiation is the mean over the preceding "
                       "hour.\n\n")
    with src.open("a") as fh:
        fh.write(f"- `{name}` | {url} | retrieved {datetime.now(timezone.utc).isoformat()}\n")


def _seconds(index: pd.DatetimeIndex) -> np.ndarray:
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    return np.asarray((index.tz_convert("UTC") - epoch) / pd.Timedelta(seconds=1), dtype=float)


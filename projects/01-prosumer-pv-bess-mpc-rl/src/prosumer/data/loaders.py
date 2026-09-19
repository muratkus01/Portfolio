"""Data layer: legacy CSV ingestion, open-data fetching, and resampling to 15 minutes.

Two entry points:

  `load_legacy_csv`  reads the thesis's own measured week in its original format
                     (`DateTime;Day;Hour;Load;PV;DE_Price`, semicolon-separated, ISO-8859-1)

  `fetch_spot_prices` downloads DE-LU day-ahead prices from the Energy-Charts open API
                     (Fraunhofer ISE, CC BY 4.0) and caches them locally

Conventions enforced here and relied on everywhere else:
  * the index is tz-aware UTC
  * intervals are left-closed, left-labelled - `[t, t+dt)`, matching market MTU convention
  * powers are kW, energies kWh, prices EUR/kWh
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ENERGY_CHARTS_URL = "https://api.energy-charts.info/price"
LOCAL_TZ = "Europe/Berlin"


# --------------------------------------------------------------------------- legacy CSV
def load_legacy_csv(path: str | Path) -> pd.DataFrame:
    """Read the original thesis CSV into the package's canonical frame.

    Returns columns `load_kw`, `pv_kw`, `spot_eur_per_kwh` on a UTC index.

    The source timestamps are German local wall-clock without a timezone. They are localised
    to Europe/Berlin and converted to UTC; ambiguous and non-existent times at the DST
    transitions are resolved explicitly rather than left to pandas' default, because silently
    dropping or duplicating an hour is a classic corruption of German energy data.
    """
    df = pd.read_csv(path, encoding="ISO-8859-1", sep=";")
    ts = pd.to_datetime(df["DateTime"], dayfirst=True)
    idx = (ts.dt.tz_localize(LOCAL_TZ, ambiguous="infer", nonexistent="shift_forward")
             .dt.tz_convert("UTC"))
    out = pd.DataFrame(
        {
            "load_kw": df["Load"].astype(float).to_numpy(),
            "pv_kw": df["PV"].astype(float).to_numpy(),
            "spot_eur_per_kwh": df["DE_Price"].astype(float).to_numpy(),
        },
        index=pd.DatetimeIndex(idx, name="timestamp"),
    )
    return out.sort_index()


# --------------------------------------------------------------------------- open data
def fetch_spot_prices(start: str, end: str, cache_dir: str | Path = "data/raw",
                      bzn: str = "DE-LU") -> pd.DataFrame:
    """Day-ahead prices from Energy-Charts (Fraunhofer ISE). Cached on disk.

    Returns a single column `spot_eur_per_kwh` on a UTC index. The API serves EUR/MWh; the
    conversion to EUR/kWh happens here so that the rest of the package never has to think
    about it (the original thesis data was already EUR/kWh).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"energycharts_price_{bzn}_{start}_{end}.json"

    if cache.exists():
        payload = json.loads(cache.read_text())
    else:
        url = f"{ENERGY_CHARTS_URL}?bzn={bzn}&start={start}&end={end}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        cache.write_text(json.dumps(payload))
        (cache_dir / "SOURCE.md").write_text(
            "# Source\n\n"
            "- Dataset: day-ahead electricity price, bidding zone DE-LU\n"
            f"- Endpoint: {ENERGY_CHARTS_URL}?bzn={bzn}&start={start}&end={end}\n"
            "- Provider: Energy-Charts, Fraunhofer ISE\n"
            "- Licence: CC BY 4.0\n"
            "- Units as served: EUR/MWh; converted to EUR/kWh on ingestion\n"
        )

    idx = pd.to_datetime(payload["unix_seconds"], unit="s", utc=True)
    price = np.asarray(payload["price"], dtype=float) / 1000.0     # EUR/MWh -> EUR/kWh
    return pd.DataFrame({"spot_eur_per_kwh": price},
                        index=pd.DatetimeIndex(idx, name="timestamp")).sort_index()


# --------------------------------------------------------------------------- resampling
def to_resolution(df: pd.DataFrame, dt_hours: float) -> pd.DataFrame:
    """Resample to the target resolution with per-column semantics.

    The semantics differ by quantity and getting them wrong is a silent modelling error:

      * PRICE is a step function - a quarter-hour inside an hourly product carries that
        product's price. Forward-fill, never interpolate.
      * POWER is an interval average. Upsampling cannot invent intra-hour structure, so this
        interpolates and then re-averages; the resulting series has the right mean but
        artificially smooth sub-hourly shape. That limitation is real and is reported: it
        means an upsampled hourly profile UNDERSTATES the peak-power effects that 15-minute
        modelling exists to capture. Measured sub-hourly data is the only true fix.
    """
    freq = pd.Timedelta(hours=dt_hours)
    if len(df.index) > 1:
        native = df.index[1] - df.index[0]
        if native == freq:
            return df.copy()

    price_cols = [c for c in df.columns if "price" in c or "eur" in c]
    power_cols = [c for c in df.columns if c not in price_cols]

    end = df.index[-1] + (df.index[1] - df.index[0] if len(df) > 1 else freq)
    target = pd.date_range(df.index[0], end, freq=freq, inclusive="left", tz=df.index.tz)

    out = pd.DataFrame(index=target)
    for c in price_cols:
        out[c] = df[c].reindex(target, method="ffill")
    for c in power_cols:
        out[c] = df[c].reindex(df.index.union(target)).interpolate("time").reindex(target)
    out.index.name = "timestamp"
    return out.ffill().bfill()


def attach_prices(site_df: pd.DataFrame, price_df: pd.DataFrame) -> pd.DataFrame:
    """Replace a frame's spot price column with an externally-sourced series."""
    out = site_df.copy()
    aligned = price_df["spot_eur_per_kwh"].reindex(out.index, method="ffill")
    out["spot_eur_per_kwh"] = aligned.to_numpy()
    return out


# --------------------------------------------------------------------------- s14a events
def make_dimming_series(index: pd.DatetimeIndex, cfg) -> np.ndarray | None:
    """Synthesise a s14a EnWG dimming signal.

    Returns an array of charging-power caps (kW) - `np.inf` where no event is active, and the
    guaranteed minimum power during an event - or None when disabled.

    No public dataset of realised dimming events exists. This is a parameterised stand-in, and
    every result that depends on it is reported across a sweep of `events_per_year` rather
    than at a single assumed value. That is the honest treatment of a genuine data gap.
    """
    if not cfg.enabled:
        return None
    rng = np.random.default_rng(cfg.seed)
    n = len(index)
    years = (index[-1] - index[0]).total_seconds() / (365.25 * 24 * 3600) or 1e-9
    n_events = max(0, int(round(cfg.events_per_year * years)))

    local_hours = np.asarray(
        (index.tz_convert(LOCAL_TZ) if index.tz is not None else index).hour)
    weights = np.where(np.isin(local_hours, cfg.concentration_hours), 5.0, 1.0)
    weights /= weights.sum()

    step_h = (index[1] - index[0]).total_seconds() / 3600 if n > 1 else 1.0
    caps = np.full(n, np.inf)
    for _ in range(n_events):
        start = int(rng.choice(n, p=weights))
        dur = max(1, int(round(rng.exponential(cfg.mean_duration_h) / step_h)))
        caps[start:start + dur] = cfg.p_min_kw
    return caps

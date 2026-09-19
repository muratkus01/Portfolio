"""Assemble the 15-minute site dataset for 2024 to 2026.

One frame on a UTC, left-labelled 15-minute index with:

    load_kw            BDEW H0 profile, 3221 kWh/a, Bavarian holidays, local clock time
    pv_kw              PV produced (pvlib on Open-Meteo 15-minute weather)
    pv_fc_kw           PV the controller expects: the same model on the weather forecast
                       issued one day earlier (Open-Meteo previous-runs archive, hourly,
                       held constant within the hour)
    spot_eur_per_kwh   EPEX day-ahead DE-LU. Hourly products until 2025-09-30 (held
                       constant over the four quarter-hours), quarter-hourly from 2025-10-01
    price_mtu_min      market time unit of the price in force (60 or 15)

Retail prices are NOT stored: they are a tariff choice, built by `market.tariff`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .loaders import fetch_spot_prices
from .pv import PVSystem, pv_power
from .slp import h0_profile
from .weather import fetch_weather_actual, fetch_weather_forecast

MTU_SWITCH = pd.Timestamp("2025-09-30 22:00", tz="UTC")   # 2025-10-01 00:00 CEST


def build_site_dataset(start: str = "2024-01-01", end: str = "2026-09-18",
                       pv_system: PVSystem = PVSystem(), annual_kwh: float = 3221.0,
                       raw_dir: str | Path = "data/raw") -> pd.DataFrame:
    """Build (or rebuild) the dataset. Network access is needed once per source."""
    raw = Path(raw_dir)
    load = h0_profile(start, end, annual_kwh=annual_kwh, holidays="BY",
                      local_tz="Europe/Berlin")
    idx = load.index

    actual = fetch_weather_actual(start, end, cache_dir=raw / "weather")
    pv = pv_power(actual, pv_system).reindex(idx).fillna(0.0)

    fc_weather = fetch_weather_forecast(start, end, lead_days=1, cache_dir=raw / "weather")
    pv_fc_hourly = pv_power(fc_weather.dropna(), pv_system)
    pv_fc = pv_fc_hourly.reindex(idx, method="ffill", limit=3)

    spot = fetch_spot_prices(start, end, cache_dir=raw / "prices")["spot_eur_per_kwh"]
    spot = spot[~spot.index.duplicated()]
    mtu = pd.Series(np.where(idx >= MTU_SWITCH, 15, 60), index=idx)

    df = pd.DataFrame({
        "load_kw": load,
        "pv_kw": pv,
        "pv_fc_kw": pv_fc,
        "spot_eur_per_kwh": spot.reindex(idx, method="ffill"),
        "price_mtu_min": mtu,
    }, index=idx)
    return df


HTW_MAIN_PROFILE = "H31"   # recommended by HTW for its fit to the seasonal SLP shape


def with_measured_load(df: pd.DataFrame, htw_profiles: pd.DataFrame,
                       household: str = HTW_MAIN_PROFILE, annual_kwh: float = 3221.0
                       ) -> pd.DataFrame:
    """Make a measured HTW household the TRUE load and the SLP its forecast.

    `load_kw` becomes the HTW profile replayed on the dataset's calendar and scaled to
    `annual_kwh`, so only the shape and variability differ from the thesis household.
    The standard load profile moves to `load_fc_kw`, which B3 and the RL agent use as the
    load forecast. Screen profiles with `htw.pv_screen` first.
    """
    from .htw import replay_on_calendar
    local = df.index.tz_convert("Europe/Berlin")
    start = local[0].normalize().tz_localize(None).date().isoformat()
    end = (local[-1].normalize().tz_localize(None) + pd.Timedelta(days=1)).date().isoformat()
    true_load = replay_on_calendar(htw_profiles[household], start, end, annual_kwh)
    out = df.copy()
    out["load_fc_kw"] = df["load_kw"]
    out["load_kw"] = true_load.reindex(df.index).to_numpy()
    out.attrs["household"] = household
    return out


def save_dataset(df: pd.DataFrame, path: str | Path = "data/processed/site_2024_2026.parquet"
                 ) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def load_dataset(path: str | Path = "data/processed/site_2024_2026.parquet") -> pd.DataFrame:
    return pd.read_parquet(path)

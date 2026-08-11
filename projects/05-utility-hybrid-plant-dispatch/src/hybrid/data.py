"""Build a hybrid-plant dataset from real German system data.

Rather than simulating wind and PV from NWP - which is Phase 5 work and needs a GRIB pipeline
- the groundwork derives per-unit capacity factors from the ACTUAL national wind and solar
generation published by Energy-Charts, divided by installed capacity. That gives realistic
15-minute shapes, real correlation between wind and PV, and real correlation with price,
which is what the dispatch problem is actually about.

The limitation is honest and important: a national capacity factor is far SMOOTHER than any
single site, because it aggregates across the whole country. A single plant sees deeper lulls
and sharper ramps. So this dataset understates both curtailment frequency and forecast
difficulty, and therefore understates the value of storage. Site-level generation data is the
top acquisition priority for this project, exactly as its README says.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))

# Installed capacity in Germany, used to turn generation into a capacity factor. Approximate
# mid-year values; the exact figure only rescales the profile, and the plant is then scaled to
# its own rating, so the result is insensitive to a few per cent here.
INSTALLED_MW = {2023: {"wind": 68000.0, "pv": 82000.0},
                2024: {"wind": 72000.0, "pv": 99000.0}}


def load_year(year: int, days: int | None = None,
              dt: float = 0.25) -> pd.DataFrame:
    """Return a frame with `wind_cf`, `pv_cf` (per unit) and `price_eur_mwh`."""
    import datakit

    start, end = f"{year}-01-01", f"{year + 1}-01-01"
    power = datakit.public_power(start, end)
    price = datakit.day_ahead_price(start, end)

    def pick(*names) -> pd.Series:
        for n in names:
            for c in power.columns:
                if c.lower() == n.lower():
                    return power[c]
        raise KeyError(f"none of {names} in {list(power.columns)}")

    wind = pick("Wind onshore") + pick("Wind offshore")
    pv = pick("Solar")

    cap = INSTALLED_MW.get(year, INSTALLED_MW[2024])
    df = pd.DataFrame({
        "wind_cf": (wind / cap["wind"]).clip(0, 1),
        "pv_cf": (pv / cap["pv"]).clip(0, 1),
    })

    # prices are hourly; hold them across the quarter-hours of their product
    df["price_eur_mwh"] = price["price_eur_per_mwh"].reindex(df.index, method="ffill")
    df = df.dropna()

    target = pd.date_range(df.index[0], df.index[-1], freq=f"{int(dt * 60)}min", tz="UTC")
    df = df.reindex(df.index.union(target)).interpolate("time").reindex(target)
    df["price_eur_mwh"] = price["price_eur_per_mwh"].reindex(target, method="ffill")

    if days is not None:
        df = df.iloc[:int(days * 24 / dt)]
    return df.dropna()


def to_plant(df: pd.DataFrame, wind_mw: float, pv_mw: float) -> tuple:
    """Scale per-unit capacity factors to a specific plant's ratings."""
    return (df["wind_cf"].to_numpy() * wind_mw,
            df["pv_cf"].to_numpy() * pv_mw,
            df["price_eur_mwh"].to_numpy())


def describe(wind: np.ndarray, pv: np.ndarray, price: np.ndarray,
             conn_mw: float, dt: float) -> str:
    gen = wind + pv
    over = (gen > conn_mw).mean() * 100
    spill = np.sum(np.clip(gen - conn_mw, 0, None)) * dt
    return (f"mean gen {gen.mean():5.1f} MW | peak {gen.max():5.1f} MW | "
            f"over connection in {over:4.1f}% of steps ({spill:,.0f} MWh unstorable) | "
            f"price mean {price.mean():5.1f} EUR/MWh, negative in "
            f"{(price < 0).mean() * 100:.1f}% of steps")

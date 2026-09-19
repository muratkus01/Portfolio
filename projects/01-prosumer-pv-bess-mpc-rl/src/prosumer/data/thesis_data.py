"""The published thesis input year (Munich household, hourly 2024).

The file is public in the thesis repository and is fetched from the immutable tag
`thesis-v1.0`, then cached under `data/raw/thesis/`. Nothing is redistributed here.

Time convention. The thesis timestamps are CET without daylight saving (UTC+1 all year):
the 2024 EPEX prices in the file match the Energy-Charts series exactly under that reading
and under no other. They are converted to the package's UTC index on ingestion.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import pandas as pd

THESIS_TAG = "thesis-v1.0"
THESIS_RAW = ("https://raw.githubusercontent.com/muratkus01/optimization/"
              f"{THESIS_TAG}/01_techno-economic_analysis/data/2024_HSS_Baveria.csv")
THESIS_TZ = "Etc/GMT-1"          # POSIX sign convention: Etc/GMT-1 is UTC+1


def fetch_thesis_year(cache_dir: str | Path = "data/raw/thesis") -> Path:
    """Download the thesis input CSV once and return the cached path."""
    cache_dir = Path(cache_dir)
    path = cache_dir / "2024_HSS_Baveria.csv"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(THESIS_RAW, timeout=60) as resp:
            path.write_bytes(resp.read())
        (cache_dir / "SOURCE.md").write_text(
            "# Source\n\n"
            f"- File: {THESIS_RAW}\n"
            "- Published with the M.Sc. thesis 'Techno-Economic Assessment of Residential BESS "
            "with PV and Dynamic Tariffs' (Murat Kus, 2025)\n"
            "- Timestamps: CET without daylight saving (UTC+1)\n")
    return path


def load_thesis_year(path: str | Path | None = None, pv_column: str = "PV_Bayern_8"
                     ) -> pd.DataFrame:
    """Canonical frame on a UTC index.

    Columns: `load_kw`, `pv_kw`, `spot_eur_per_kwh` (EPEX), and the thesis's own retail
    prices `ep_buy`, `ep_sell` (EUR/kWh), kept so the tariff reconstruction can be verified.
    """
    path = fetch_thesis_year() if path is None else Path(path)
    df = pd.read_csv(path, sep=";", encoding="ISO-8859-1")
    local = pd.to_datetime(df["DateTime"], dayfirst=True).dt.tz_localize(THESIS_TZ)
    idx = pd.DatetimeIndex(local.dt.tz_convert("UTC"), name="timestamp")
    return pd.DataFrame({
        "load_kw": df["P_Load"].astype(float).to_numpy(),
        "pv_kw": df[pv_column].astype(float).to_numpy(),
        "spot_eur_per_kwh": df["EP_epex"].astype(float).to_numpy(),
        "ep_buy": df["EP_buy"].astype(float).to_numpy(),
        "ep_sell": df["EP_sell"].astype(float).to_numpy(),
    }, index=idx)

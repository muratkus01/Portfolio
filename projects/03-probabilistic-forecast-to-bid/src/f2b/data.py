"""The real dataset: portfolio output, published TSO forecasts, prices and imbalance prices.

The portfolio is the German fleet scaled to `PortfolioConfig` (200 MW wind, 100 MW PV): the
national wind and PV feed-in from Energy-Charts, divided by the installed capacity of that
month, times the portfolio's ratings. A national aggregate is far smoother than any real
300 MW portfolio, so forecast errors and imbalance volumes here are SMALLER than a real
portfolio would see. Every table built on this dataset carries that caveat.

Columns of `build_dataset` (15-minute steps, UTC, labelled by interval start):

  wind_cf, pv_cf         national actual feed-in / monthly installed capacity
  truth_mw               portfolio output, MW
  tso_da_mw, tso_id_mw,  the published TSO day-ahead, intraday and current forecasts of the
  tso_cur_mw             same fleet, scaled the same way (benchmark only, see datakit)
  price_da               day-ahead price, EUR/MWh (hourly products until 2025-09-30)
  load_mw, residual_mw   national load and load minus wind and PV, MW
  rebap, rebap_long,     reBAP for short and long balance groups and the intraday price index
  id_aep                 ID AEP, EUR/MWh, from netztransparenz CSVs if present (NaN otherwise)
"""
from __future__ import annotations

import sys
import time
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RunConfig

_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))

TSO_TYPES = {"tso_da_mw": "day-ahead", "tso_id_mw": "intraday", "tso_cur_mw": "current"}


def build_dataset(start: str, end: str, run: RunConfig,
                  netztransparenz_dir: Path | None = None) -> pd.DataFrame:
    """Every series for [start, end) on one 15-minute UTC index, fetched year by year."""
    import datakit

    index = pd.date_range(start, end, freq="15min", tz="UTC", inclusive="left")
    years = range(index[0].year, index[-1].year + 1)
    power = pd.concat([_patient(datakit.public_power, *_year(y)) for y in years])
    power = power[~power.index.duplicated()].reindex(index)
    cap = datakit.installed_capacity_at(index)

    wind_cf = ((_col(power, "Wind onshore") + _col(power, "Wind offshore")) / cap["wind"])
    pv_cf = _col(power, "Solar") / cap["pv"]
    df = pd.DataFrame({"wind_cf": wind_cf.clip(0, 1), "pv_cf": pv_cf.clip(0, 1)}, index=index)
    df["truth_mw"] = run.portfolio.wind_mw * df["wind_cf"] + run.portfolio.pv_mw * df["pv_cf"]

    for column, kind in TSO_TYPES.items():
        parts = {pt: pd.concat([_patient(datakit.public_power_forecast, *_year(y), pt, kind)
                                for y in years]).iloc[:, 0]
                 for pt in ("wind_onshore", "wind_offshore", "solar")}
        fc = {pt: s[~s.index.duplicated()].reindex(index) for pt, s in parts.items()}
        wind = (fc["wind_onshore"] + fc["wind_offshore"]) / cap["wind"]
        pv = fc["solar"] / cap["pv"]
        df[column] = (run.portfolio.wind_mw * wind.clip(0, 1)
                      + run.portfolio.pv_mw * pv.clip(0, 1))

    price = pd.concat([_patient(datakit.day_ahead_price, *_year(y)) for y in years])
    price = price[~price.index.duplicated()]["price_eur_per_mwh"]
    # An hourly product covers its four quarter-hours; from 2025-10-01 the index is 15-min
    # already, so forward-filling changes nothing there.
    df["price_da"] = price.reindex(index, method="ffill", limit=3)
    df["load_mw"] = _col(power, "Load")
    df["residual_mw"] = df["load_mw"] - (_col(power, "Wind onshore")
                                         + _col(power, "Wind offshore") + _col(power, "Solar"))

    imb = read_netztransparenz(netztransparenz_dir) if netztransparenz_dir else None
    for c in ("rebap", "rebap_long", "id_aep"):
        df[c] = imb[c].reindex(index) if imb is not None and c in imb else np.nan
    return df


# Value columns of the netztransparenz exports, by the start of their header.
NTP_COLUMNS = {"ID AEP": "id_aep"}
UTC_OFFSET_H = {"UTC": 0, "MEZ": 1, "MESZ": 2}


def read_netztransparenz(folder: Path) -> pd.DataFrame | None:
    """Imbalance and intraday prices from CSVs downloaded by hand from netztransparenz.de.

    The site exports `Datum von;(Uhrzeit) von;Zeitzone;(Uhrzeit) bis;Zeitzone;<value>`,
    semicolon-separated with decimal commas. Files may overlap; a later file wins. A file
    whose value column is not recognised raises rather than being skipped.
    """
    files = sorted(Path(folder).glob("*.csv"))
    if not files:
        return None
    series: dict[str, list[pd.Series]] = {}
    for path in files:
        column, values = _read_ntp_csv(path)
        series.setdefault(column, []).append(values)
    out = {}
    for column, parts in series.items():
        joined = pd.concat(parts)
        out[column] = joined[~joined.index.duplicated(keep="last")].sort_index()
    return pd.DataFrame(out)


def _read_ntp_csv(path: Path) -> tuple[str, pd.Series]:
    raw = pd.read_csv(path, sep=";", decimal=",", encoding="utf-8-sig", dtype=str)
    value_col = raw.columns[-1]
    column = next((c for key, c in NTP_COLUMNS.items() if value_col.startswith(key)), None)
    if column is None:
        raise ValueError(f"{path.name}: unknown value column {value_col!r}")
    zones = set(raw.iloc[:, 2].unique())
    if not zones <= set(UTC_OFFSET_H):
        raise ValueError(f"{path.name}: unexpected time zones {sorted(zones)}")
    local = pd.to_datetime(raw.iloc[:, 0] + " " + raw.iloc[:, 1], format="%d.%m.%Y %H:%M")
    offset = pd.to_timedelta(raw.iloc[:, 2].map(UTC_OFFSET_H), unit="h")
    start = pd.DatetimeIndex(local - offset).tz_localize("UTC")
    values = pd.to_numeric(raw[value_col].str.replace(".", "", regex=False)
                           .str.replace(",", ".", regex=False), errors="coerce")
    return column, pd.Series(values.to_numpy(float), index=start, name=column)


def _year(y: int) -> tuple[str, str]:
    return f"{y}-01-01", f"{y + 1}-01-01"


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    for c in frame.columns:
        if c.lower() == name.lower():
            return frame[c]
    raise KeyError(f"{name!r} not in {list(frame.columns)[:12]}")


def _patient(fn, *args, retries: int = 4, pause_s: float = 60.0):
    """Call a datakit fetcher, waiting out Energy-Charts' rate limit (HTTP 429)."""
    for attempt in range(retries):
        try:
            return fn(*args)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries - 1:
                raise
            time.sleep(pause_s)
    raise RuntimeError("unreachable")

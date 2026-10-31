"""Shared open-data access for the portfolio.

One module, no credentials required, covering the German market and system data that
Projects 01, 02 and 03 depend on. Every function returns a tz-aware UTC-indexed DataFrame and
caches its raw response under `cache_dir`, alongside a `SOURCE.md` recording the endpoint,
retrieval time and licence - so a result can always be traced back to the bytes it came from.

Sources and licences (see docs/02-data-sources.md for the full catalogue):

  Energy-Charts (Fraunhofer ISE)   CC BY 4.0     prices, generation, load, installed capacity
  SMARD (Bundesnetzagentur)        free reuse    official market data, 15-min
  ENTSO-E Transparency             free reuse    pan-European; needs a free API token

Deliberately NOT included: anything requiring a commercial licence. Every project in this
portfolio must run end-to-end on open data, so a commercial source is never a dependency.

    python datakit.py --check          verify every endpoint is reachable
    python datakit.py --pull 2025      download the standard portfolio dataset for a year
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

EC = "https://api.energy-charts.info"
DEFAULT_CACHE = Path(__file__).parent / "cache"
TIMEOUT = 90


# ------------------------------------------------------------------ plumbing
def _get_json(url: str, cache_dir: Path, tag: str, licence: str) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = urllib.parse.quote(tag, safe="")
    cache = cache_dir / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read().decode())
    cache.write_text(json.dumps(payload))

    src = cache_dir / "SOURCE.md"
    line = (f"- `{tag}` | {url} | retrieved {datetime.now(timezone.utc).isoformat()} "
            f"| licence: {licence}\n")
    if not src.exists():
        src.write_text("# Data provenance\n\nEvery file in this directory, with its origin.\n\n")
    with src.open("a") as fh:
        fh.write(line)
    return payload


def _frame(payload: dict, value_key: str, column: str, scale: float = 1.0) -> pd.DataFrame:
    idx = pd.to_datetime(payload["unix_seconds"], unit="s", utc=True)
    return pd.DataFrame({column: np.asarray(payload[value_key], dtype=float) * scale},
                        index=pd.DatetimeIndex(idx, name="timestamp")).sort_index()


# ------------------------------------------------------------------ datasets
def day_ahead_price(start: str, end: str, bzn: str = "DE-LU",
                    cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Day-ahead price, EUR/MWh. Used by Projects 01, 02 and 03.

    Hourly products until 2025-09-30, quarter-hourly from 2025-10-01: the index changes step.
    """
    url = f"{EC}/price?bzn={bzn}&start={start}&end={end}"
    p = _get_json(url, cache_dir, f"price_{bzn}_{start}_{end}", "CC BY 4.0 (Energy-Charts)")
    return _frame(p, "price", "price_eur_per_mwh")


def public_power(start: str, end: str, country: str = "de",
                 cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Generation by production type plus load, MW. Projects 02 and 03.

    Returns one column per production type as served by Energy-Charts, including
    'Wind offshore', 'Wind onshore', 'Solar', 'Load' and 'Residual load'.
    """
    url = f"{EC}/public_power?country={country}&start={start}&end={end}"
    p = _get_json(url, cache_dir, f"public_power_{country}_{start}_{end}",
                  "CC BY 4.0 (Energy-Charts)")
    idx = pd.to_datetime(p["unix_seconds"], unit="s", utc=True)
    cols = {s["name"] if isinstance(s["name"], str) else s["name"][0]:
            np.asarray(s["data"], dtype=float) for s in p["production_types"]}
    return pd.DataFrame(cols, index=pd.DatetimeIndex(idx, name="timestamp")).sort_index()


def total_power(start: str, end: str, country: str = "de",
                cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Generation by production type INCLUDING self-consumption, MW, shaped like public_power.

    `public_power` counts what is fed into the public grid, so its 'Solar' misses rooftop
    output used on site. Here 'Solar' is total generation, and the load column is
    'Load (incl. self-consumption)'. Use it as the numerator of a capacity factor against
    installed capacity; use public_power where grid feed-in is meant, as forecasts of
    feed-in are.
    """
    url = f"{EC}/total_power?country={country}&start={start}&end={end}"
    p = _get_json(url, cache_dir, f"total_power_{country}_{start}_{end}",
                  "CC BY 4.0 (Energy-Charts)")
    idx = pd.to_datetime(p["unix_seconds"], unit="s", utc=True)
    cols = {s["name"] if isinstance(s["name"], str) else s["name"][0]:
            np.asarray(s["data"], dtype=float) for s in p["production_types"]}
    return pd.DataFrame(cols, index=pd.DatetimeIndex(idx, name="timestamp")).sort_index()


def installed_power(country: str = "de", time_step: str = "yearly",
                    cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Installed capacity by technology, in GW as served, indexed by Energy-Charts' time labels.

    Monthly labels read 'MM.YYYY'. `installed_power_monthly` converts to MW on a time index.
    """
    url = f"{EC}/installed_power?country={country}&time_step={time_step}"
    p = _get_json(url, cache_dir, f"installed_{country}_{time_step}",
                  "CC BY 4.0 (Energy-Charts)")
    return pd.DataFrame({s["name"] if isinstance(s["name"], str) else s["name"][0]: s["data"]
                         for s in p["production_types"]}, index=p["time"])


def installed_power_monthly(country: str = "de",
                            cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Wind and PV capacity per month, MW, indexed by month start.

    Columns: `wind` (onshore + offshore) and `pv` (the AC rating) for fleet-wide capacity
    factors, plus `wind_onshore`, `wind_offshore`, `pv_ac` and `pv_dc` for a plant that is
    one of them. AC is the right base for AC feed-in; the DC rating is about 10 % higher and
    would understate every PV capacity factor.
    """
    raw = installed_power(country, "monthly", cache_dir)
    out = pd.DataFrame({"wind": raw["Wind onshore"] + raw["Wind offshore"],
                        "pv": raw["Solar AC"],
                        "wind_onshore": raw["Wind onshore"],
                        "wind_offshore": raw["Wind offshore"],
                        "pv_ac": raw["Solar AC"],
                        "pv_dc": raw["Solar DC"]}).astype(float) * 1000.0
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index, format="%m.%Y", utc=True),
                                 name="month")
    return out.dropna()


def installed_capacity_at(index: pd.DatetimeIndex, country: str = "de",
                          cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Every column of `installed_power_monthly`, in MW, at each timestamp of `index`.

    Energy-Charts does not say whether a monthly value holds at the start, middle or end of
    the month, so each is placed mid-month. The timing error is then at most half a month of
    additions, under 1 % of PV capacity in 2024 to 2026. Raises if `index` leaves the range.
    """
    monthly = installed_power_monthly(country, cache_dir)
    anchors = _epoch_seconds(monthly.index + pd.Timedelta(days=15))
    t = _epoch_seconds(index)
    if t.min() < anchors[0] or t.max() > anchors[-1] + 31 * 86400:
        raise ValueError(f"index {index[0]} .. {index[-1]} outside the published capacity "
                         f"months {monthly.index[0]:%Y-%m} .. {monthly.index[-1]:%Y-%m}")
    return pd.DataFrame({c: np.interp(t, anchors, monthly[c].to_numpy())
                         for c in monthly.columns}, index=index)


def _epoch_seconds(index: pd.DatetimeIndex) -> np.ndarray:
    """Seconds since 1970 as floats, independent of the index's storage unit (s, ms, us, ns)."""
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    return np.asarray((index.tz_convert("UTC") - epoch) / pd.Timedelta(seconds=1), dtype=float)


def public_power_forecast(start: str, end: str, production_type: str, forecast_type: str,
                          country: str = "de",
                          cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Published TSO forecast of one production type, MW, 15-minute.

    `production_type`: 'solar', 'wind_onshore' or 'wind_offshore'.
    `forecast_type`: 'day-ahead', 'intraday' or 'current'.

    Energy-Charts serves the transparency-platform forecasts without a publication time. The
    EU transparency rules (Regulation 543/2013, Art. 14) make the day-ahead forecast due by
    18:00 on D-1, AFTER the 12:00 day-ahead gate closure, and the intraday forecast during
    the delivery day. Use them as a benchmark, not as an input known at a gate.
    """
    url = (f"{EC}/public_power_forecast?country={country}&production_type={production_type}"
           f"&forecast_type={forecast_type}&start={start}&end={end}")
    p = _get_json(url, cache_dir,
                  f"power_forecast_{country}_{production_type}_{forecast_type}_{start}_{end}",
                  "CC BY 4.0 (Energy-Charts)")
    return _frame(p, "forecast_values", f"{production_type}_{forecast_type}")


# Installed capacity in Germany (MW) used to normalize generation to per-unit capacity factor.
# Provenance: Fraunhofer ISE Energy-Charts and BNetzA Marktstammdatenregister (MaStR) mid-year values.
# 2023: Wind 68,000 MW, Solar 82,000 MW (Fraunhofer ISE 2023 annual report)
# 2024: Wind 72,000 MW, Solar 99,000 MW (Energy-Charts installed_power mid-2024)
# 2025: Wind 75,000 MW, Solar 110,000 MW (Energy-Charts installed_power mid-2025)
# 2026: Wind 78,000 MW, Solar 118,000 MW (Part-year anchor based on BNetzA additions through Q3 2026)
INSTALLED_CAPACITY_MW: dict[int, dict[str, float]] = {
    2023: {"wind": 68000.0, "pv": 82000.0},
    2024: {"wind": 72000.0, "pv": 99000.0},
    2025: {"wind": 75000.0, "pv": 110000.0},
    2026: {"wind": 78000.0, "pv": 118000.0},  # part-year anchor
}


def get_installed_capacity(year: int) -> dict[str, float]:
    """Return German installed capacity (MW) for wind and solar.

    Raises ValueError if year is not registered. Silent year substitution is strictly forbidden.
    """
    if year not in INSTALLED_CAPACITY_MW:
        raise ValueError(
            f"Installed capacity anchor not available for year {year}. "
            f"Registered years: {sorted(INSTALLED_CAPACITY_MW.keys())}. "
            "No silent year substitution is permitted."
        )
    return INSTALLED_CAPACITY_MW[year]


def cross_border_flows(start: str, end: str, country: str = "de",
                       cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    """Net cross-border physical flows, MW. Candidate price-formation features."""
    url = f"{EC}/cbpf?country={country}&start={start}&end={end}"
    p = _get_json(url, cache_dir, f"cbpf_{country}_{start}_{end}",
                  "CC BY 4.0 (Energy-Charts)")
    idx = pd.to_datetime(p["unix_seconds"], unit="s", utc=True)
    cols = {s["name"] if isinstance(s["name"], str) else s["name"][0]:
            np.asarray(s["data"], dtype=float) for s in p["countries"]}
    return pd.DataFrame(cols, index=pd.DatetimeIndex(idx, name="timestamp")).sort_index()


def entsoe(document_type: str, start: str, end: str, token: str,
           domain: str = "10Y1001A1001A82H", extra: dict | None = None) -> str:
    """Raw ENTSO-E Transparency query. Returns XML text.

    Needs a free API token (request one from the ENTSO-E helpdesk). Kept thin on purpose:
    each project parses the document types it needs. `domain` defaults to DE-LU.

    Document types used across this portfolio:
        A44  day-ahead prices              A65  total load
        A75  actual generation per type    A69  wind/solar forecast
        A85  imbalance prices              A81  balancing capacity
    """
    params = {"documentType": document_type, "in_Domain": domain, "out_Domain": domain,
              "periodStart": start, "periodEnd": end, "securityToken": token}
    params.update(extra or {})
    url = "https://web-api.tp.entsoe.eu/api?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        return resp.read().decode()


# ------------------------------------------------------------------ derived
def imbalance_proxy(power: pd.DataFrame) -> pd.DataFrame:
    """Residual-load ramp and renewable share - the drivers of imbalance price tails.

    The real reBAP comes from netztransparenz.de. This derived frame only drives the synthetic
    imbalance price of Project 03's parametric ablation.
    """
    out = pd.DataFrame(index=power.index)
    load_col = next((c for c in power.columns if c.lower() == "load"), None)
    ren = [c for c in power.columns
           if any(k in c.lower() for k in ("wind", "solar", "hydro", "biomass"))]
    if load_col is None or not ren:
        raise ValueError(f"unexpected columns: {list(power.columns)[:10]}")

    out["load_mw"] = power[load_col]
    out["renewable_mw"] = power[ren].sum(axis=1)
    out["residual_mw"] = out["load_mw"] - out["renewable_mw"]
    out["residual_ramp_mw"] = out["residual_mw"].diff()
    out["renewable_share"] = out["renewable_mw"] / out["load_mw"].replace(0, np.nan)
    return out


# ------------------------------------------------------------------ CLI
CHECKS = [
    ("day-ahead price DE-LU", lambda c: day_ahead_price("2025-06-01", "2025-06-03", cache_dir=c)),
    ("public power DE", lambda c: public_power("2025-06-01", "2025-06-03", cache_dir=c)),
    ("installed capacity DE", lambda c: installed_power(cache_dir=c)),
    ("cross-border flows DE", lambda c: cross_border_flows("2025-06-01", "2025-06-03", cache_dir=c)),
]


def check(cache_dir: Path) -> int:
    failures = 0
    for name, fn in CHECKS:
        try:
            df = fn(cache_dir)
            shape = f"{df.shape[0]} rows x {df.shape[1]} cols"
            print(f"  OK    {name:<28} {shape}")
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as ex:
            print(f"  FAIL  {name:<28} {type(ex).__name__}: {ex}")
            failures += 1
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} sources reachable")
    return failures


def pull(year: int, cache_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = f"{year}-01-01", f"{year + 1}-01-01"

    price = day_ahead_price(start, end, cache_dir=cache_dir)
    price.to_parquet(out_dir / f"price_de_lu_{year}.parquet")
    print(f"price          {len(price):6d} rows | mean {price.iloc[:, 0].mean():7.2f} EUR/MWh "
          f"| negative {(price.iloc[:, 0] < 0).mean() * 100:.1f}% of steps")

    power = public_power(start, end, cache_dir=cache_dir)
    power.to_parquet(out_dir / f"public_power_de_{year}.parquet")
    print(f"public power   {len(power):6d} rows | {power.shape[1]} production types")

    feats = imbalance_proxy(power)
    feats.to_parquet(out_dir / f"system_features_de_{year}.parquet")
    print(f"system feats   {len(feats):6d} rows | mean renewable share "
          f"{feats['renewable_share'].mean() * 100:.1f}%")
    print(f"\nwritten to {out_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify every endpoint is reachable")
    ap.add_argument("--pull", type=int, metavar="YEAR", help="download the standard dataset")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "processed")
    args = ap.parse_args()

    if args.check:
        return 1 if check(args.cache) else 0
    if args.pull:
        pull(args.pull, args.cache, args.out)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

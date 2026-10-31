"""Build a hybrid-plant dataset from real German system data.

Rather than simulating wind and PV from NWP - which is Phase 5 work and needs a GRIB pipeline
- the groundwork derives per-unit capacity factors from the ACTUAL national wind and solar
generation published by Energy-Charts, divided by the installed capacity of the same month.
That gives realistic 15-minute shapes, real correlation between wind and PV, and real
correlation with price, which is what the dispatch problem is actually about.

The limitation is honest and important: a national capacity factor is far SMOOTHER than any
single site, because it aggregates across the whole country. A single plant sees deeper lulls
and sharper ramps. So this dataset understates both curtailment frequency and forecast
difficulty, and therefore understates the value of storage. Site-level generation data is the
top acquisition priority for this project, exactly as its README says.

The level runs low as well: the national fleet mixes older turbines and rooftop PV with new
plant, so a new plant would produce more than these factors say.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))

# Energy-Charts serves installed_power in GW and public_power in MW. The payload names no
# unit: onshore wind for December 2024 reads 63.589.
_GW_TO_MW = 1000.0


def _total_power(start: str, end: str, country: str = "de") -> pd.DataFrame:
    """Generation by production type INCLUDING self-consumption, MW, from Energy-Charts.

    public_power counts feed-in to the public grid, so its 'Solar' leaves out the rooftop
    output used on site, which the installed PV fleet still counts. total_power counts it. A
    stand-in for datakit.total_power, which another branch adds and which will replace this
    helper: it calls the same endpoint through datakit's own plumbing with the same cache tag,
    so the two share one cache file and give the same numbers.

    Args:
        start, end: dates as Energy-Charts takes them, e.g. "2024-01-01" and "2025-01-01".
        country: Energy-Charts country code, e.g. "de".

    Returns:
        Frame on a UTC index, one column per production type, e.g. 'Solar', 'Wind onshore'.
    """
    import datakit

    # datakit loads as a package (repo root on the path) or as a module (its folder on the
    # path), and only the module that defines public_power carries the private _get_json.
    plumbing = sys.modules[datakit.public_power.__module__]
    url = f"{datakit.EC}/total_power?country={country}&start={start}&end={end}"
    p = plumbing._get_json(url, datakit.DEFAULT_CACHE, f"total_power_{country}_{start}_{end}",
                           "CC BY 4.0 (Energy-Charts)")
    idx = pd.to_datetime(p["unix_seconds"], unit="s", utc=True)
    cols = {s["name"] if isinstance(s["name"], str) else s["name"][0]:
            np.asarray(s["data"], dtype=float) for s in p["production_types"]}
    return pd.DataFrame(cols, index=pd.DatetimeIndex(idx, name="timestamp")).sort_index()


def _capacity_by_month(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Installed German onshore wind and PV (DC) capacity for the month of each timestamp.

    Args:
        index: tz-aware timestamps of the feed-in, e.g. 15-minute stamps in UTC.

    Returns:
        Frame on `index` with columns `wind_mw` and `pv_mw`, in MW.

    Raises:
        ValueError: when Energy-Charts has no value for a month that `index` covers.
    """
    import datakit

    cap = datakit.installed_power(country="de", time_step="monthly")
    # Energy-Charts' December value equals its yearly one, so each monthly value reads as the
    # fleet at the end of the month. A month's factors therefore run low by about half of the
    # fleet's relative growth in that month.
    # Months are counted in Berlin time, the same local calendar that cuts the year's window:
    # 23:00 UTC on 31 December is already January.
    months = index.tz_convert("Europe/Berlin").strftime("%m.%Y")
    out = cap[["Wind onshore", "Solar DC"]].reindex(months) * _GW_TO_MW
    missing = sorted(set(months[out.isna().any(axis=1).to_numpy()]))
    if missing:
        # No fallback to a neighbouring month: that would be a silent guess. datakit caches
        # forever, so this usually means the cached capacity payload predates the feed-in.
        raise ValueError(f"Energy-Charts installed_power has no monthly capacity for {missing}; "
                         "if datakit's cached installed_de_monthly.json is older than the "
                         "feed-in, delete it and run again")
    out.columns = ["wind_mw", "pv_mw"]
    out.index = index
    return out


def load_year(year: int, days: int | None = None,
              dt: float = 0.25) -> pd.DataFrame:
    """Return national per-unit wind and PV capacity factors and the day-ahead price.

    Args:
        year: calendar year, e.g. 2024.
        days: keep only the first `days` days, or None for the whole year.
        dt: step length in hours, e.g. 0.25 for quarter-hours.

    Returns:
        Frame on a UTC index that covers `year` in Berlin time (its first `days` when given),
        with `wind_cf` (onshore feed-in over that month's onshore capacity), `pv_cf` (solar
        generation, self-consumption included, over that month's DC capacity),
        `pv_feed_in_cf` (solar feed-in to the public grid over the same capacity, the PV
        weight of the market-value proxy), all clipped to [0, 1], and `price_eur_mwh`.

    Raises:
        ValueError: when the capacity series lacks a month the feed-in covers.
    """
    import datakit

    start, end = f"{year}-01-01", f"{year + 1}-01-01"
    # Energy-Charts includes the end date, so the feed-in runs to 23:45 on 1 January of the
    # next year, Berlin time. Cut it at the local New Year: that keeps the calendar year, and
    # keeps next January out of the capacity lookup below.
    new_year = pd.Timestamp(end, tz="Europe/Berlin")
    power = datakit.public_power(start, end)
    power = power[power.index < new_year]
    total = _total_power(start, end)
    total = total[total.index < new_year]
    price = datakit.day_ahead_price(start, end)

    def pick(frame: pd.DataFrame, name: str) -> pd.Series:
        for c in frame.columns:
            if c.lower() == name.lower():
                return frame[c]
        raise KeyError(f"{name!r} not in {list(frame.columns)}")

    # The plant's wind farm is onshore (its applicable value is the EEG § 36 onshore one), so
    # its factor is onshore feed-in over onshore capacity. Offshore wind runs at a higher
    # capacity factor and would inflate it.
    wind = pick(power, "Wind onshore")
    # PV is divided by its DC rating, the usual capacity-factor denominator, so `pv_mw` is a
    # DC (MWp) rating, and the numerator must count what that whole fleet generates. Its
    # public_power 'Solar' is feed-in to the public grid, which leaves out the self-consumed
    # rooftop output that the DC fleet still counts, so it understated a fully exporting
    # plant, most of all in winter; total_power's 'Solar' includes it.
    pv = pick(total, "Solar")
    # The market-value proxy keeps public feed-in as its PV weight, as before
    # (`market.market_values`). Energy-Charts' total solar is its public solar times one
    # factor per month, so a monthly value comes out the same with either weight; an annual
    # value does not, as the factors weight the months differently.
    pv_feed_in = pick(power, "Solar")

    # Each quarter-hour is divided by its own month's fleet. One annual figure near the
    # year-end fleet made January 2024 PV factors 14 % too low (99.0 GW where 84.9 GW stood).
    cap = _capacity_by_month(power.index)
    df = pd.DataFrame({
        "wind_cf": (wind / cap["wind_mw"]).clip(0, 1),
        "pv_cf": (pv / cap["pv_mw"]).clip(0, 1),
        "pv_feed_in_cf": (pv_feed_in / cap["pv_mw"]).clip(0, 1),
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
    """One line on the window: generation, how much of it lies above the connection, prices.

    The energy above the connection is what the plant must curtail or store; the battery can
    take part of it, so none of it is unstorable as such.
    wind, pv: available generation per step, MW. price: day-ahead price per step, EUR/MWh.
    conn_mw: the connection's export limit, MW. dt: step length in hours.
    Returns the line.
    """
    gen = wind + pv
    over = (gen > conn_mw).mean() * 100
    spill = np.sum(np.clip(gen - conn_mw, 0, None)) * dt
    return (f"mean gen {gen.mean():5.1f} MW | peak {gen.max():5.1f} MW | "
            f"over connection in {over:4.1f}% of steps ({spill:,.0f} MWh above it) | "
            f"price mean {price.mean():5.1f} EUR/MWh, negative in "
            f"{(price < 0).mean() * 100:.1f}% of steps")

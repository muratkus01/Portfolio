"""Capacity normalisation in the data layer, offline on synthetic Energy-Charts payloads.

The fixture replaces datakit's one network call with a stub that serves small payloads in the
API's own shape, so datakit's real parsers run and no test touches the network.
"""
from __future__ import annotations

import sys

import pandas as pd
import pytest

import datakit
from hybrid.data import load_year

# One day around a month change: 22:00 UTC on 30 June 2024 is midnight on 1 July in Berlin.
START = pd.Timestamp("2024-06-30 12:00", tz="UTC")
JULY = pd.Timestamp("2024-06-30 22:00", tz="UTC")
STEPS = 96

# Constant national feed-in, MW, as public_power serves it.
FEED_IN_MW = {"Wind onshore": 30_000.0, "Wind offshore": 3_000.0, "Solar": 24_000.0}

# Installed capacity as installed_power serves it: one value per month, in GW. December is the
# year-end fleet, which is roughly what the old single annual figure held.
CAPACITY_GW = {
    "time": ["05.2024", "06.2024", "07.2024", "12.2024"],
    "Wind onshore": [58.0, 60.0, 62.5, 75.0],
    "Wind offshore": [8.0, 8.0, 8.5, 9.0],
    "Solar DC": [76.0, 80.0, 96.0, 120.0],
    "Solar AC": [71.0, 75.0, 90.0, 110.0],
}


def _payloads(capacity: dict) -> list[tuple[str, dict]]:
    """Energy-Charts payloads keyed by a URL fragment that identifies the request."""
    t0 = int(START.timestamp())
    hours = [t0 + 3600 * k for k in range(STEPS // 4)]
    power = {"unix_seconds": [t0 + 900 * k for k in range(STEPS)],
             "production_types": [{"name": name, "data": [mw] * STEPS}
                                  for name, mw in FEED_IN_MW.items()]}
    price = {"unix_seconds": hours, "price": [50.0] * len(hours)}
    installed = {"time": capacity["time"],
                 "production_types": [{"name": name, "data": values}
                                      for name, values in capacity.items() if name != "time"]}
    return [("/public_power?", power), ("/price?", price),
            ("/installed_power?country=de&time_step=monthly", installed)]


@pytest.fixture
def energy_charts(monkeypatch):
    """Return a function that makes datakit serve the synthetic payloads for one capacity table."""
    module = sys.modules[datakit.public_power.__module__]

    def serve(capacity: dict = CAPACITY_GW) -> None:
        routes = _payloads(capacity)

        def fake_get_json(url: str, *_args) -> dict:
            for fragment, payload in routes:
                if fragment in url:
                    return payload
            raise AssertionError(f"unexpected Energy-Charts request: {url}")

        monkeypatch.setattr(module, "_get_json", fake_get_json)

    return serve


# ------------------------------------------------------------------ capacity normalisation
def test_wind_factor_is_onshore_feed_in_over_the_months_onshore_fleet(energy_charts):
    """June wind is June onshore feed-in over June's onshore fleet.

    Regression test. The factor used to be onshore plus offshore feed-in over one hand-typed
    annual figure near the year-end fleet. The plant is onshore, and a mid-year month must not
    be divided by December's fleet.
    """
    energy_charts()
    june = load_year(2024).loc[:JULY - pd.Timedelta("15min")]
    assert june["wind_cf"].to_numpy() == pytest.approx(30_000 / 60_000)


def test_pv_factor_is_solar_feed_in_over_the_months_dc_fleet(energy_charts):
    """June PV is solar feed-in over June's DC fleet, not the AC or the year-end figure.

    Regression test. One annual figure near the year-end fleet made January 2024 PV factors
    14 % too low (99.0 GW where 84.9 GW stood).
    """
    energy_charts()
    june = load_year(2024).loc[:JULY - pd.Timedelta("15min")]
    assert june["pv_cf"].to_numpy() == pytest.approx(24_000 / 80_000)


def test_each_quarter_hour_takes_the_capacity_of_its_local_month(energy_charts):
    """The month changes at midnight in Berlin, so 22:00 UTC on 30 June already counts as July."""
    energy_charts()
    df = load_year(2024)
    last_june = df.loc[JULY - pd.Timedelta("15min")]
    first_july = df.loc[JULY]
    assert (last_june["wind_cf"], last_june["pv_cf"]) == pytest.approx(
        (30_000 / 60_000, 24_000 / 80_000))
    assert (first_july["wind_cf"], first_july["pv_cf"]) == pytest.approx(
        (30_000 / 62_500, 24_000 / 96_000))


def test_a_month_missing_from_the_capacity_payload_is_an_error(energy_charts):
    """datakit caches payloads forever, so a capacity file can end before the feed-in does.

    Borrowing a neighbouring month would be a silent guess, so the gap must fail loudly.
    """
    keep = [i for i, month in enumerate(CAPACITY_GW["time"]) if month != "07.2024"]
    energy_charts({key: [values[i] for i in keep] for key, values in CAPACITY_GW.items()})
    with pytest.raises(ValueError, match="07.2024"):
        load_year(2024)

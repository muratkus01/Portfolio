"""Capacity normalisation and the year's window in the data layer, offline on synthetic
Energy-Charts payloads.

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
# Generation including self-consumption, MW, as total_power serves it. Its solar is larger by
# the rooftop output used on site; its onshore wind differs here only so that the tests can
# tell which endpoint a factor was read from.
TOTAL_MW = {"Wind onshore": 31_000.0, "Wind offshore": 3_000.0, "Solar": 30_000.0}

# Installed capacity as installed_power serves it: one value per month, in GW. December is the
# year-end fleet, which is roughly what the old single annual figure held.
CAPACITY_GW = {
    "time": ["05.2024", "06.2024", "07.2024", "12.2024"],
    "Wind onshore": [58.0, 60.0, 62.5, 75.0],
    "Wind offshore": [8.0, 8.0, 8.5, 9.0],
    "Solar DC": [76.0, 80.0, 96.0, 120.0],
    "Solar AC": [71.0, 75.0, 90.0, 110.0],
}

# One day across New Year: 23:00 UTC on 31 December 2024 is midnight in Berlin. Energy-Charts
# includes the end date, so the payload it serves for 2024 runs on into 1 January 2025.
NEW_YEAR = pd.Timestamp("2024-12-31 23:00", tz="UTC")
# The same table with January 2025 added, so that a frame running into 2025 can be built.
CAPACITY_GW_TO_JAN_2025 = {
    "time": CAPACITY_GW["time"] + ["01.2025"],
    "Wind onshore": CAPACITY_GW["Wind onshore"] + [75.5],
    "Wind offshore": CAPACITY_GW["Wind offshore"] + [9.0],
    "Solar DC": CAPACITY_GW["Solar DC"] + [121.5],
    "Solar AC": CAPACITY_GW["Solar AC"] + [111.5],
}


def _payloads(capacity: dict, start: pd.Timestamp) -> list[tuple[str, dict]]:
    """Energy-Charts payloads from `start` on, keyed by a URL fragment that names the request."""
    t0 = int(start.timestamp())
    hours = [t0 + 3600 * k for k in range(STEPS // 4)]
    def generation(mw_by_type: dict) -> dict:
        return {"unix_seconds": [t0 + 900 * k for k in range(STEPS)],
                "production_types": [{"name": name, "data": [mw] * STEPS}
                                     for name, mw in mw_by_type.items()]}

    price = {"unix_seconds": hours, "price": [50.0] * len(hours)}
    installed = {"time": capacity["time"],
                 "production_types": [{"name": name, "data": values}
                                      for name, values in capacity.items() if name != "time"]}
    return [("/public_power?", generation(FEED_IN_MW)), ("/total_power?", generation(TOTAL_MW)),
            ("/price?", price), ("/installed_power?country=de&time_step=monthly", installed)]


@pytest.fixture
def energy_charts(monkeypatch):
    """Return a function that makes datakit serve the synthetic payloads for one capacity table
    and one start time, and returns the list of (url, cache tag) requests it then serves."""
    module = sys.modules[datakit.public_power.__module__]

    def serve(capacity: dict = CAPACITY_GW, start: pd.Timestamp = START) -> list:
        routes = _payloads(capacity, start)
        requests = []

        def fake_get_json(url: str, cache_dir, tag: str, *_args) -> dict:
            requests.append((url, tag))
            for fragment, payload in routes:
                if fragment in url:
                    return payload
            raise AssertionError(f"unexpected Energy-Charts request: {url}")

        monkeypatch.setattr(module, "_get_json", fake_get_json)
        return requests

    return serve


# ------------------------------------------------------------------ capacity normalisation
def test_wind_factor_is_onshore_feed_in_over_the_months_onshore_fleet(energy_charts):
    """June wind is June onshore feed-in over June's onshore fleet.

    Regression test. The factor used to be onshore plus offshore feed-in over one hand-typed
    annual figure near the year-end fleet. The plant is onshore, and a mid-year month must not
    be divided by December's fleet. It stays on public_power's onshore feed-in.
    """
    energy_charts()
    june = load_year(2024).loc[:JULY - pd.Timedelta("15min")]
    assert june["wind_cf"].to_numpy() == pytest.approx(30_000 / 60_000)


def test_pv_factor_is_total_solar_generation_over_the_months_dc_fleet(energy_charts):
    """June PV is total solar generation over June's DC fleet, not feed-in, the AC rating or
    the year-end figure.

    Regression tests. One annual figure near the year-end fleet made January 2024 PV factors
    14 % too low (99.0 GW where 84.9 GW stood). And public_power's 'Solar' is feed-in to the
    public grid only: it leaves out the self-consumed output that the DC fleet still counts,
    so the factor ran low by that share. total_power's 'Solar' includes it.
    """
    energy_charts()
    june = load_year(2024).loc[:JULY - pd.Timedelta("15min")]
    assert june["pv_cf"].to_numpy() == pytest.approx(30_000 / 80_000)


def test_total_solar_is_cached_under_the_tag_datakit_total_power_uses(energy_charts):
    """The stand-in for datakit.total_power must request the same URL under the same cache
    tag, so that the two share one cache file and the later swap fetches nothing and moves no
    number. Nothing pinned the tag before."""
    requests = energy_charts()
    load_year(2024)
    assert (f"{datakit.EC}/total_power?country=de&start=2024-01-01&end=2025-01-01",
            "total_power_de_2024-01-01_2025-01-01") in requests


def test_pv_feed_in_factor_is_public_solar_over_the_same_fleet(energy_charts):
    """The market-value proxy weights PV by public feed-in, so that series stays available."""
    energy_charts()
    june = load_year(2024).loc[:JULY - pd.Timedelta("15min")]
    assert june["pv_feed_in_cf"].to_numpy() == pytest.approx(24_000 / 80_000)


def test_each_quarter_hour_takes_the_capacity_of_its_local_month(energy_charts):
    """The month changes at midnight in Berlin, so 22:00 UTC on 30 June already counts as July."""
    energy_charts()
    df = load_year(2024)
    last_june = df.loc[JULY - pd.Timedelta("15min")]
    first_july = df.loc[JULY]
    assert (last_june["wind_cf"], last_june["pv_cf"]) == pytest.approx(
        (30_000 / 60_000, 30_000 / 80_000))
    assert (first_july["wind_cf"], first_july["pv_cf"]) == pytest.approx(
        (30_000 / 62_500, 30_000 / 96_000))


def test_a_month_missing_from_the_capacity_payload_is_an_error(energy_charts):
    """datakit caches payloads forever, so a capacity file can end before the feed-in does.

    Borrowing a neighbouring month would be a silent guess, so the gap must fail loudly.
    """
    keep = [i for i, month in enumerate(CAPACITY_GW["time"]) if month != "07.2024"]
    energy_charts({key: [values[i] for i in keep] for key, values in CAPACITY_GW.items()})
    with pytest.raises(ValueError, match="07.2024"):
        load_year(2024)


# ------------------------------------------------------------------ the year's window
def test_the_year_ends_at_midnight_on_new_year_in_berlin(energy_charts):
    """The API serves 1 January 2025 with the 2024 payload; load_year(2024) must drop it.

    Regression test. The extra day used to stay in, so a whole year ran one day long and any
    per-month figure got a thirteenth month.
    """
    energy_charts(CAPACITY_GW_TO_JAN_2025, start=NEW_YEAR - pd.Timedelta("3h"))
    assert load_year(2024).index[-1] == NEW_YEAR - pd.Timedelta("15min")


def test_the_year_needs_no_capacity_from_the_next_january(energy_charts):
    """A capacity payload fetched in December has no January yet, and the year must not need it.

    Regression test. With the extra day in, load_year(2024) raised ValueError for 01.2025.
    """
    energy_charts(start=NEW_YEAR - pd.Timedelta("3h"))
    assert load_year(2024).index[-1] == NEW_YEAR - pd.Timedelta("15min")

"""Data layer for the 2024 to 2026 extension: load profile, PV model, weather labelling.

Calendar and PV physics tests are synthetic and always run. Tests that need the BDEW table or
the thesis input year fetch them once from the thesis tag and skip when offline.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from prosumer.data.slp import _easter, bavarian_holidays, h0_profile
from prosumer.data.weather import _to_frame

ROOT = Path(__file__).parents[1]


# --------------------------------------------------------------------------- calendar
def test_easter_known_dates():
    assert [_easter(y) for y in (2024, 2025, 2026)] == [
        dt.date(2024, 3, 31), dt.date(2025, 4, 20), dt.date(2026, 4, 5)]


def test_bavarian_holidays_2025():
    h = bavarian_holidays(2025)
    assert len(h) == 13
    assert dt.date(2025, 6, 19) in h           # Corpus Christi
    assert dt.date(2025, 8, 15) in h           # Assumption Day (Munich)


# --------------------------------------------------------------------------- load
@pytest.fixture(scope="module")
def h0_table():
    from prosumer.data.slp import fetch_h0_table
    try:
        return fetch_h0_table(ROOT / "data" / "raw" / "slp")
    except OSError as exc:                                          # pragma: no cover
        pytest.skip(f"BDEW table not available offline: {exc}")


def test_slp_thesis_mode_reproduces_thesis_load(h0_table):
    """Thesis settings (no holidays, CET all year) give the published P_Load exactly."""
    from prosumer.data.thesis_data import fetch_thesis_year, load_thesis_year
    try:
        year = load_thesis_year(fetch_thesis_year(ROOT / "data" / "raw" / "thesis"))
    except OSError as exc:                                          # pragma: no cover
        pytest.skip(f"thesis input year not available offline: {exc}")
    q = h0_profile("2024-01-01", "2025-01-01", holidays=None, local_tz="Etc/GMT-1",
                   table=h0_table)
    hourly = q.resample("1h").mean()
    np.testing.assert_allclose(hourly.to_numpy(), year["load_kw"].to_numpy(), atol=1e-9)


def test_slp_local_time_dst_and_energy(h0_table):
    q = h0_profile("2025-01-01", "2026-01-01", table=h0_table)
    per_day = q.index.tz_convert("Europe/Berlin").normalize().value_counts()
    assert sorted(per_day.value_counts().to_dict().items()) == [(92, 1), (96, 363), (100, 1)]
    assert q.sum() * 0.25 == pytest.approx(3221.0, rel=1e-9)


def test_slp_holiday_is_a_sunday(h0_table):
    """Whit Monday 2025 carries the Sunday profile, the Tuesday after it does not."""
    q = h0_profile("2025-06-08", "2025-06-11", table=h0_table)
    loc = q.index.tz_convert("Europe/Berlin")
    sun, mon, tue = (q[loc.date == dt.date(2025, 6, d)].to_numpy() for d in (8, 9, 10))
    ratio = mon / sun
    assert np.allclose(ratio, ratio[0], rtol=1e-3)          # same shape, dynamization only
    assert not np.allclose(tue / sun, (tue / sun)[0], rtol=1e-3)


# --------------------------------------------------------------------------- weather, PV
def test_open_meteo_labels_are_shifted_to_interval_start():
    block = {"time": ["2025-06-01T12:15", "2025-06-01T12:30"], "shortwave_radiation": [1, 2]}
    df = _to_frame(block, "15min")
    assert df.index[0] == pd.Timestamp("2025-06-01 12:00", tz="UTC")
    assert list(df.columns) == ["ghi"]


def test_pv_clear_sky_physics():
    pvlib = pytest.importorskip("pvlib")
    from prosumer.data.pv import PVSystem, pv_power
    idx = pd.date_range("2025-06-21", periods=96, freq="15min", tz="UTC")
    loc = pvlib.location.Location(48.1372, 11.5756, altitude=520)
    cs = loc.get_clearsky(idx + pd.Timedelta("7.5min"))
    cs.index = idx
    wx = pd.DataFrame({"ghi": cs["ghi"], "dni": cs["dni"], "dhi": cs["dhi"],
                       "temp_air": 20.0, "wind_speed": 7.2}, index=idx)
    p = pv_power(wx, PVSystem(kwp=8.0))
    assert p.max() <= 8.0 and p.min() >= 0.0
    assert p.iloc[:8].sum() == 0.0                          # midnight to 02:00 UTC
    peak_cet = (p.idxmax() + pd.Timedelta("7.5min")).tz_convert("Etc/GMT-1")
    assert 11.5 <= peak_cet.hour + peak_cet.minute / 60 <= 13.0   # solar noon ~12:15 CET
    assert 50 < p.sum() * 0.25 < 70                        # clear June day, 8 kWp


# --------------------------------------------------------------------------- dataset
def test_built_dataset_has_mtu_switch():
    path = ROOT / "data" / "processed" / "site_2024_2026.parquet"
    if not path.exists():
        pytest.skip("dataset not built (python -m prosumer.cli build-data)")
    df = pd.read_parquet(path)
    hourly = df.loc[:"2025-09-30 21:45"].spot_eur_per_kwh.to_numpy()
    assert np.all(hourly.reshape(-1, 4).std(axis=1) < 1e-12)   # one price per hour
    after = df.loc["2025-10-01":].spot_eur_per_kwh.to_numpy()
    after = after[: len(after) // 4 * 4].reshape(-1, 4)
    assert np.mean(after.std(axis=1) > 1e-9) > 0.9            # quarter-hour products

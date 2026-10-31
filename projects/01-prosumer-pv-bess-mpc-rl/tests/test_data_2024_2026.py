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

from prosumer.data.slp import _COLUMNS, _easter, bavarian_holidays, h0_profile, state_holidays
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


@pytest.mark.parametrize("code, n_2025", [
    ("BW", 12), ("BY", 13), ("BE", 11), ("BB", 12), ("HB", 10), ("HH", 10), ("HE", 10),
    ("MV", 11), ("NI", 10), ("NW", 11), ("RP", 11), ("SL", 12), ("SN", 11), ("ST", 11),
    ("SH", 10), ("TH", 11),
])
def test_every_state_has_its_statutory_holiday_count_in_2025(code, n_2025):
    assert len(state_holidays(code, 2025)) == n_2025


def test_holidays_introduced_after_2010_do_not_leak_into_the_htw_year():
    """The HTW profiles are from 2010; a holiday added later must not be applied to them."""
    assert dt.date(2010, 10, 31) not in state_holidays("NI", 2010)
    assert dt.date(2024, 10, 31) in state_holidays("NI", 2024)
    assert dt.date(2010, 3, 8) not in state_holidays("BE", 2010)
    assert dt.date(2024, 3, 8) in state_holidays("BE", 2024)
    assert dt.date(2022, 3, 8) not in state_holidays("MV", 2022)
    assert dt.date(2023, 3, 8) in state_holidays("MV", 2023)
    assert dt.date(2010, 9, 20) not in state_holidays("TH", 2010)
    assert dt.date(2025, 5, 8) in state_holidays("BE", 2025)
    assert dt.date(2024, 5, 8) not in state_holidays("BE", 2024)


def test_day_of_repentance_is_the_wednesday_before_23_november():
    got = [next(d for d in state_holidays("SN", y) if d.month == 11)
           for y in (2010, 2024, 2025, 2026)]
    assert got == [dt.date(2010, 11, 17), dt.date(2024, 11, 20),
                   dt.date(2025, 11, 19), dt.date(2026, 11, 18)]


def test_unknown_state_code_is_rejected():
    table = pd.DataFrame(1.0, index=range(96), columns=_COLUMNS)
    with pytest.raises(ValueError):
        state_holidays("XX", 2025)
    with pytest.raises(ValueError):
        h0_profile("2025-01-01", "2025-01-02", holidays="Bavaria", table=table)


def test_h0_profile_applies_the_chosen_state_calendar():
    """Reformation Day 2025, a Friday, is a holiday in Saxony and a working day in Bavaria."""
    table = pd.DataFrame({c: [2.0 if c.endswith("sunday") else 1.0] * 96 for c in _COLUMNS})
    day = {code: h0_profile("2025-10-30", "2025-11-01", holidays=code, table=table)
           for code in ("SN", "BY")}
    local = {code: q.index.tz_convert("Europe/Berlin").day for code, q in day.items()}
    thursday = day["SN"][local["SN"] == 30].sum() / day["BY"][local["BY"] == 30].sum()
    friday = day["SN"][local["SN"] == 31].sum() / day["BY"][local["BY"] == 31].sum()
    assert thursday == pytest.approx(1.0, abs=0.02)     # only the annual scaling differs
    assert friday == pytest.approx(2.0, abs=0.04)


# --------------------------------------------------------------------------- load
@pytest.fixture(scope="module")
def h0_table():
    from prosumer.data.slp import fetch_h0_table
    try:
        return fetch_h0_table(ROOT / "data" / "raw" / "slp")
    except (OSError, ImportError) as exc:                                          # pragma: no cover
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


# --------------------------------------------------------------------------- prices vs thesis
PRICE_CACHE = ROOT / "data" / "raw" / "prices" / "energycharts_price_DE-LU_2024-01-01_2026-09-18.json"


@pytest.fixture(scope="module")
def thesis_and_cached_prices() -> tuple[pd.DataFrame, pd.Series]:
    """The thesis input year and the Energy-Charts series that `prosumer build-data` cached."""
    if not PRICE_CACHE.exists():
        pytest.skip("price cache absent; run prosumer build-data")
    from prosumer.data.loaders import fetch_spot_prices
    from prosumer.data.thesis_data import fetch_thesis_year
    try:
        path = fetch_thesis_year(ROOT / "data" / "raw" / "thesis")
    except OSError as exc:                                          # pragma: no cover
        pytest.skip(f"thesis input year not available offline: {exc}")
    raw = pd.read_csv(path, sep=";", encoding="ISO-8859-1")
    cached = fetch_spot_prices("2024-01-01", "2026-09-18", cache_dir=PRICE_CACHE.parent)
    return raw, cached["spot_eur_per_kwh"]


def _matching_hours(raw: pd.DataFrame, cached: pd.Series, clock: str) -> tuple[int, int]:
    """(hours whose EPEX price equals the cached price, hours compared), labels read on `clock`."""
    labels = pd.to_datetime(raw["DateTime"], dayfirst=True)
    stamps = pd.DatetimeIndex(labels.dt.tz_localize(clock, ambiguous="NaT", nonexistent="NaT"))
    keep = ~stamps.isna()
    ours = cached.reindex(stamps[keep]).to_numpy()
    theirs = raw["EP_epex"].astype(float).to_numpy()[keep]
    return int((np.abs(ours - theirs) < 1e-9).sum()), int(keep.sum())


def test_cached_2024_prices_equal_the_thesis_epex_column_read_as_cet(thesis_and_cached_prices):
    """README data table: the 2024 prices are the thesis prices, hour for hour.

    The thesis labels read as CET without daylight saving (UTC+1, as `load_thesis_year`
    reads them) give all 8,784 hours of 2024 the cached price, to float rounding.
    """
    raw, cached = thesis_and_cached_prices
    assert _matching_hours(raw, cached, "Etc/GMT-1") == (8784, 8784)


@pytest.mark.parametrize("clock", ["UTC", "Europe/Berlin"])
def test_thesis_epex_column_does_not_fit_another_clock(thesis_and_cached_prices, clock):
    """Read as UTC or as legal local time, fewer than half of the hours match."""
    raw, cached = thesis_and_cached_prices
    matched, compared = _matching_hours(raw, cached, clock)
    assert matched < 0.5 * compared

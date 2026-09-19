"""Measured-load layer: calendar replay and the PV screen. Synthetic, always runs."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import pandas as pd

from prosumer.data.htw import FLOOR_KW, pv_screen, replay_on_calendar


def _year_2010(values_fn):
    idx = pd.date_range("2010-01-01", periods=365 * 96, freq="15min",
                        tz="Etc/GMT-1").tz_convert("UTC")
    return idx, values_fn(idx.tz_convert("Etc/GMT-1"))


def test_replay_matches_weekday_and_local_clock():
    """Encode the weekday and the LOCAL (DST) hour of 2010 in the value and read it back."""
    idx, _ = _year_2010(lambda l: 0)
    local = idx.tz_convert("Europe/Berlin")
    src = pd.Series(local.weekday * 100 + local.hour + 1.0, index=idx)
    out = replay_on_calendar(src, "2025-03-24", "2025-04-07", annual_kwh=None)
    loc = out.index.tz_convert("Europe/Berlin")
    np.testing.assert_array_equal(out.to_numpy() // 100, loc.weekday)
    # daylight saving starts 2025-03-30: 08:00 local must still carry the 08:00 source value
    eight = (loc.hour == 8) & (loc.minute == 0)
    assert np.all(out[eight].to_numpy() % 100 == 9)


def test_replay_holiday_uses_a_sunday_and_scaling():
    idx, _ = _year_2010(lambda l: 0)
    local = idx.tz_convert("Europe/Berlin")
    src = pd.Series(np.where(local.weekday == 6, 2.0, 1.0), index=idx)
    out = replay_on_calendar(src, "2025-06-08", "2025-06-11", annual_kwh=None)
    loc = out.index.tz_convert("Europe/Berlin")
    assert np.all(out[loc.date == dt.date(2025, 6, 9)] == 2.0)       # Whit Monday
    assert np.all(out[loc.date == dt.date(2025, 6, 10)] == 1.0)


def test_replay_scales_to_annual_energy():
    idx, _ = _year_2010(lambda l: 0)
    flat = pd.Series(0.5, index=idx)
    full = replay_on_calendar(flat, "2025-01-01", "2026-01-01", annual_kwh=3221.0)
    assert full.sum() * 0.25 == pytest.approx(3221.0, rel=1e-9)


def test_pv_screen_flags_a_household_with_pv_behind_the_meter():
    idx, _ = _year_2010(lambda l: 0)
    local = idx.tz_convert("Etc/GMT-1")
    days = pd.Index(local.date).unique()
    rng = np.random.default_rng(1)
    ghi = pd.Series(rng.uniform(5, 30, len(days)), index=days)
    g = pd.Series(local.date).map(ghi).to_numpy()
    base = 0.3 + 0.4 * ((local.hour >= 18) & (local.hour < 22)) + 0.2 * ((local.hour >= 11) & (local.hour < 15))
    sun = np.clip(np.sin(np.pi * (local.hour + local.minute / 60 - 6) / 14), 0, None)
    pv = 2.5 * sun * (g / 30) * local.month.isin([4, 5, 6, 7, 8, 9])
    gross = pd.Series(base, index=idx)
    net = np.maximum(base - pv, FLOOR_KW * 0.7)             # export pinned at the floor
    profiles = pd.DataFrame({"gross": gross, "net": net}, index=idx)
    s = pv_screen(profiles, ghi)
    assert bool(s.loc["net", "pv_suspect"]) and not bool(s.loc["gross", "pv_suspect"])

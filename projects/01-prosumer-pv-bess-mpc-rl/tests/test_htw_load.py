"""Measured-load layer: calendar replay and the PV screen.

Synthetic tests always run. The last test replays a real HTW household and skips without the
gitignored 15-minute parquet.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest
import pandas as pd

from prosumer.data.htw import FLOOR_KW, file_labels, pv_screen, replay_on_calendar

HTW_PARQUET = Path(__file__).parents[1] / "data" / "processed" / "htw_74_15min_2010.parquet"


def _year_2010(values_fn):
    """An index built exactly as `load_htw_15min` builds it, and values from its file labels."""
    idx = pd.date_range("2010-01-01", periods=365 * 96, freq="15min",
                        tz="Etc/GMT-1").tz_convert("UTC")
    return idx, values_fn(file_labels(idx))


def test_file_labels_give_96_slots_every_day_including_dst_days():
    idx, _ = _year_2010(lambda l: 0)
    labels = file_labels(idx)
    per_day = pd.Series(1, index=labels.date).groupby(level=0).sum()
    assert len(per_day) == 365 and (per_day == 96).all()
    assert labels[0] == pd.Timestamp("2010-01-01 00:00") and labels.tz is None


@pytest.mark.parametrize("start, end", [("2025-03-24", "2025-04-07"),     # summer time starts
                                        ("2025-06-02", "2025-06-09"),     # deep summer
                                        ("2025-10-20", "2025-11-01")])    # summer time ends
# (1 November is a Bavarian holiday and correctly replays a 2010 Sunday, so it is left out)
def test_replay_keeps_the_household_clock_across_dst(start, end):
    """Regression for the HTW clock bug: the slot a household labelled 08:00 in 2010 must be
    replayed at 08:00 local time, summer or winter. Reading the stored index through
    Europe/Berlin replayed every summer one hour late."""
    idx, labels_value = _year_2010(lambda l: l.weekday * 100 + l.hour + 1.0)
    src = pd.Series(labels_value, index=idx)
    out = replay_on_calendar(src, start, end, annual_kwh=None)
    loc = out.index.tz_convert("Europe/Berlin")
    np.testing.assert_array_equal(out.to_numpy() // 100, loc.weekday)
    np.testing.assert_array_equal(out.to_numpy() % 100, loc.hour + 1)


def test_replay_holiday_uses_a_sunday_and_scaling():
    idx, sunday = _year_2010(lambda l: np.where(l.weekday == 6, 2.0, 1.0))
    src = pd.Series(sunday, index=idx)
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


@pytest.mark.skipif(not HTW_PARQUET.exists(), reason="HTW parquet absent; see htw.py")
def test_replayed_households_wake_at_the_same_local_time_across_dst():
    """On real data: the weekday morning ramp of the replayed household mean must not move
    when summer time starts or ends in 2025; the old MEZ reading moved it by one hour. The
    mean is used because one household's own habits move it (H28 shifts by 0.7 h around
    Easter 2010); the 0.5 h threshold sits halfway between no shift and the bug."""
    from prosumer.experiments.aggregate_load_validation import half_max_time

    screen = pd.read_csv(Path(__file__).parents[1] / "reports" / "htw_pv_screen.csv", index_col=0)
    profiles = pd.read_parquet(HTW_PARQUET).drop(columns=screen.index[screen["pv_suspect"]])
    load = replay_on_calendar(profiles.mean(axis=1), "2025-02-24", "2025-11-24", annual_kwh=None)
    local = load.index.tz_convert("Europe/Berlin")
    frame = pd.DataFrame({"kw": load.to_numpy(), "day": local.date,
                          "slot": local.hour * 4 + local.minute // 15})
    days = frame.groupby(["day", "slot"])["kw"].mean().unstack().ffill(axis=1)
    weekday = np.array([d.weekday() < 5 for d in days.index])
    wake = pd.Series([half_max_time(r) for r in days.to_numpy()], index=days.index)[weekday]
    for edge in (dt.date(2025, 3, 30), dt.date(2025, 10, 26)):
        gap = np.array([(d - edge).days for d in wake.index])
        step = wake[(gap > 0) & (gap <= 21)].mean() - wake[(gap < 0) & (gap >= -21)].mean()
        assert abs(step) < 0.5, f"wake-up time jumps {step:+.2f} h at {edge}"

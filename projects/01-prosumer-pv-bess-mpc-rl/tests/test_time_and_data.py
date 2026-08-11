"""Time handling and resampling.

German energy data has two classic silent corruptions, and both get a test here:

  1. **DST transitions.** Europe/Berlin has a 23-hour day in March and a 25-hour day in
     October. Localising naive wall-clock timestamps without saying what to do about the
     duplicated and missing hours either raises, drops data, or - worst - silently shifts
     everything after the transition by an hour.

  2. **Resampling semantics.** A price is a step function; a power is an interval average.
     Interpolating a price or forward-filling a power both produce plausible-looking series
     that are wrong in ways that do not show up until the economics come out odd.

Plus a regression test for a bug caught during implementation: resampling a concatenation of
non-contiguous measured periods fabricates data for the gaps between them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prosumer.data.loaders import load_legacy_csv, to_resolution
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data" / "raw" / "legacy"


def _frame(index) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame(
        {"load_kw": np.linspace(1, 2, n),
         "pv_kw": np.linspace(0, 3, n),
         "spot_eur_per_kwh": np.linspace(0.05, 0.15, n)},
        index=index)


def test_index_is_utc_and_sorted():
    for p in sorted(DATA_DIR.glob("*.csv")):
        df = load_legacy_csv(p)
        assert str(df.index.tz) == "UTC"
        assert df.index.is_monotonic_increasing
        assert not df.index.has_duplicates


def test_dst_autumn_25_hour_day_survives():
    """The October transition duplicates a local hour; both copies must be preserved."""
    idx = pd.date_range("2024-10-27 00:00", periods=25, freq="h",
                        tz="Europe/Berlin", ambiguous="infer").tz_convert("UTC")
    df = _frame(idx)
    assert len(df) == 25
    out = to_resolution(df, 0.25)
    assert len(out) == 25 * 4
    assert out.index.is_monotonic_increasing


def test_dst_spring_23_hour_day_survives():
    """The March transition removes a local hour; the day must be 23 hours, not 24."""
    idx = pd.date_range("2024-03-31 00:00", periods=23, freq="h",
                        tz="Europe/Berlin").tz_convert("UTC")
    df = _frame(idx)
    out = to_resolution(df, 0.25)
    assert len(out) == 23 * 4


def test_price_is_stepped_not_interpolated():
    """Every quarter-hour inside an hourly product carries that product's price."""
    idx = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
    df = _frame(idx)
    out = to_resolution(df, 0.25)
    for h in range(4):
        block = out["spot_eur_per_kwh"].iloc[h * 4:(h + 1) * 4].to_numpy()
        assert np.allclose(block, block[0]), "price must be a step function, not interpolated"
        assert block[0] == pytest.approx(df["spot_eur_per_kwh"].iloc[h])


def test_power_is_interpolated_not_stepped():
    """Powers are interval averages; upsampling interpolates rather than holding."""
    idx = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
    out = to_resolution(_frame(idx), 0.25)
    block = out["load_kw"].iloc[0:4].to_numpy()
    assert not np.allclose(block, block[0]), "power must vary within the hour"


def test_resolution_roundtrip_is_identity():
    """Resampling to the resolution the data already has must not change it."""
    idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
    df = _frame(idx)
    pd.testing.assert_frame_equal(to_resolution(df, 1.0), df)


def test_noncontiguous_weeks_must_be_resampled_separately():
    """Regression test for a real bug found during implementation.

    The four measured weeks are scattered across 2024. Concatenating them and THEN resampling
    builds one continuous January-to-December index and interpolates the eleven months in
    between - turning 672 hours of measurement into 33 600 quarter-hours of mostly invented
    data. Resampling each week first and concatenating afterwards keeps only what was
    measured.
    """
    weeks = sorted(DATA_DIR.glob("*.csv"))
    if len(weeks) < 2:
        pytest.skip("needs at least two measured weeks")

    per_week = pd.concat([to_resolution(load_legacy_csv(p), 0.25) for p in weeks])
    naive = to_resolution(pd.concat([load_legacy_csv(p) for p in weeks]).sort_index(), 0.25)

    expected = sum(len(load_legacy_csv(p)) for p in weeks) * 4
    assert len(per_week) == expected
    assert len(naive) > 5 * len(per_week), \
        "expected the naive path to fabricate data - if not, this test is no longer needed"

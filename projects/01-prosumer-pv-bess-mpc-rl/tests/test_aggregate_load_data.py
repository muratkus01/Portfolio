"""The aggregate load model against the measured HTW households and the ERA5 cache.

These need two gitignored inputs: the HTW 15-minute parquet (CC BY-NC 4.0, see htw.py) and
the ERA5 daily temperatures of the 16 state sites under data/raw/weather (written by
`prosumer aggregate-load`). They skip without them and never reach the network.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from prosumer.data import aggregate_load as al

ROOT = Path(__file__).parents[1]
HTW = ROOT / "data" / "processed" / "htw_74_15min_2010.parquet"
SCREEN = ROOT / "reports" / "htw_pv_screen.csv"


def _era5_cached() -> bool:
    try:
        from prosumer.data.pv_states import STATE_SITES
    except ImportError:                                             # pragma: no cover
        return False
    folder = ROOT / "data" / "raw" / "weather"
    return all((folder / f"era5_tmean_daily_{s.latitude}_{s.longitude}_{al.ERA5_SPAN[0]}_"
                f"{al.ERA5_SPAN[1]}.csv").exists() for s in STATE_SITES.values())


needs_htw = pytest.mark.skipif(not HTW.exists(), reason="HTW parquet absent; see htw.py")
needs_all = pytest.mark.skipif(not (HTW.exists() and _era5_cached()),
                               reason="HTW parquet or ERA5 cache absent; run prosumer aggregate-load")


@pytest.fixture(scope="module")
def ctx():
    from prosumer.experiments.aggregate_load_fit import prepare
    return prepare(HTW, SCREEN)


@needs_htw
def test_htw_labels_follow_legal_clock():
    """The weekday morning ramp does not jump at the DST changes on the file clock."""
    from prosumer.experiments.aggregate_load_fit import fit_calendar_2010, htw_file_grid, pv_households
    from prosumer.experiments.aggregate_load_validation import clock_check

    dates, grid = htw_file_grid(pd.read_parquet(HTW), pv_households(SCREEN))
    national, excluded = fit_calendar_2010()
    table, _ = clock_check(grid.mean(axis=2), dates, national | excluded)
    file_clock = table[table["reading"] == "file_clock"]["step_h"].abs()
    mez = table[table["reading"] == "mez_reading"]["step_h"].abs()
    assert (file_clock < 0.25).all() and (mez > 0.75).all()


@needs_all
def test_regional_holidays_not_observed_in_2010(ctx):
    from prosumer.experiments.aggregate_load_fit import fit_from
    from prosumer.experiments.aggregate_load_validation import holiday_check

    table, _, consistent = holiday_check(ctx, fit_from(ctx))
    assert table[table["kind"] == "national"]["observed"].all()
    assert not table[table["kind"] == "regional"]["observed"].any()
    assert consistent == ["BE", "BB", "HB", "HH", "MV", "NI", "SH", "TH"]


@needs_all
def test_holiday_test_sees_a_holiday_kept_by_most_homes_but_not_by_a_few(ctx):
    """The location claim is only as strong as this: detection must rise with the share."""
    from prosumer.experiments.aggregate_load_fit import fit_from
    from prosumer.experiments.aggregate_load_validation import holiday_detection_power

    power = holiday_detection_power(ctx, fit_from(ctx), fractions=(0.1, 0.9), reps=1)
    rate = power.set_index("share_of_homes_keeping")["detection_rate"]
    assert rate[0.1] < 0.2 and rate[0.9] > 0.8


@needs_all
def test_refit_reproduces_committed_coefficients(ctx):
    from prosumer.experiments.aggregate_load_fit import fit_from

    fresh, committed = fit_from(ctx), al.load_model()
    np.testing.assert_allclose(fresh.alpha, committed.alpha, rtol=1e-9)
    np.testing.assert_allclose(fresh.energy_harm, committed.energy_harm, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(fresh.weekend_season, committed.weekend_season, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(fresh.shape, committed.shape, rtol=1e-9, atol=1e-12)
    assert fresh.beta == pytest.approx(committed.beta, rel=1e-9)
    assert fresh.a_ref == pytest.approx(committed.a_ref, rel=1e-9)


@needs_all
def test_slope_two_way_ci_excludes_zero(ctx):
    from prosumer.experiments.aggregate_load_robustness import slope_uncertainty

    table, _ = slope_uncertainty(ctx, n_boot=200, seeds=(0,))
    hi = table[(table["quantity"] == "p97.5") & (table["method"] == "two_way")]["value"].iloc[0]
    assert hi < 0


@needs_all
def test_model_beats_h0_and_same_day_mean_on_held_out_households_and_weeks(ctx):
    from prosumer.data.slp import state_holidays
    from prosumer.experiments.aggregate_load_robustness import split_half
    from prosumer.experiments.aggregate_load_validation import h0_file_grid

    h0 = h0_file_grid(ctx["dates"], state_holidays(al.FIT_PROXY, 2010))
    table, _ = split_half(ctx, h0, n_splits=10, seeds=(0,))
    assert (table["model_15min"] < table["h0_15min"]).sum() >= 9
    assert (table["model_15min"] < table["same_day_15min"]).sum() >= 9


@needs_all
def test_state_energy_stays_within_3pct_of_normal_weather_2024():
    m = al.load_model()
    for state in ("BY", "NI", "SN"):
        a = al.state_anomaly(state, "2024-01-01", "2025-01-01")
        actual = al.unit_load(m, state, "2024-01-01", "2025-01-01", a).sum()
        normal = al.unit_load(m, state, "2024-01-01", "2025-01-01", a * 0.0).sum()
        assert abs(actual / normal - 1.0) <= 0.03

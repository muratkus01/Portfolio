"""The learned forecast layer: information sets, vintages, splits, the ladder's models."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from f2b.features import EMBARGO_H, SPLITS, build_features, split_rows
from f2b.models import (
    LEVELS,
    Climatology,
    GradientBoosting,
    LinearQuantile,
    Persistence,
    finish,
)
from f2b.stages import gate_closure_steps, intraday_decision_steps
from f2b.weather import SITES, VARIABLES, VINTAGES, to_quarter_hours, vintage_for_lead

DT = 0.25


def fake_inputs(days: int = 12, seed: int = 0):
    """A small dataset and 15-minute weather frame with the real column layout."""
    index = pd.date_range("2025-05-01", periods=days * 96, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    n = len(index)
    h = index.hour.to_numpy() + index.minute.to_numpy() / 60
    df = pd.DataFrame({"wind_cf": np.clip(0.3 + 0.1 * np.sin(np.arange(n) / 50)
                                          + 0.02 * rng.standard_normal(n), 0, 1),
                       "pv_cf": np.clip(np.sin(np.pi * (h - 5) / 14), 0, 1) * 0.6}, index=index)
    df["truth_mw"] = 200 * df.wind_cf + 100 * df.pv_cf
    weather = {f"{s}|{v}|d{k}": rng.random(n) * 10
               for s in SITES for v in VARIABLES for k in VINTAGES}
    return df, pd.DataFrame(weather, index=index)


# ------------------------------------------------------------------ vintages and timing
def test_vintage_rule_never_uses_a_run_published_after_the_issue():
    leads = np.array([0.25, 0.5, 6.0, 17.75, 18.0, 18.25, 24.0, 35.75, 42.0])
    n = vintage_for_lead(leads)
    np.testing.assert_array_equal(n, [1, 1, 1, 1, 1, 2, 2, 2, 2])
    # a run made N days before the target is public 6 h later; that must precede the issue
    assert np.all(24 * n - 6 >= leads)


def test_radiation_mean_is_placed_mid_hour():
    """Open-Meteo labels an hourly radiation MEAN with the hour's end; an instantaneous value
    keeps its label. A one-hour slip here is the kind of clock error P01 had."""
    hourly_index = pd.date_range("2025-06-01 10:00", periods=3, freq="h", tz="UTC")
    hourly = pd.DataFrame({"X|shortwave_radiation|d1": [0.0, 400.0, 400.0],
                           "X|temperature_2m|d1": [0.0, 40.0, 40.0]}, index=hourly_index)
    q_index = pd.date_range("2025-06-01 10:00", periods=4, freq="15min", tz="UTC")
    q = to_quarter_hours(hourly, q_index)
    # the mean labelled 10:00 sits at 09:30 (0), the one labelled 11:00 at 10:30 (400)
    rad = q["X|shortwave_radiation|d1"].to_numpy()
    assert rad[0] == pytest.approx(400.0 * 37.5 / 60)    # 10:07:30 is 37.5 min past 09:30
    assert rad[2] == pytest.approx(400.0)                # 10:37:30 is past 10:30
    temp = q["X|temperature_2m|d1"].to_numpy()
    assert temp[0] == pytest.approx(40.0 * 7.5 / 60)     # 10:07:30, between 10:00 and 11:00


# ------------------------------------------------------------------ the information set
@pytest.mark.parametrize("stage", ["DA", "ID-0.5h"])
def test_features_ignore_everything_after_the_information_set(stage):
    """Corrupt every measurement after the last one available at the decision, and every
    weather value except the vintage that was public then: the features must not move."""
    df, wq = fake_inputs()
    dec = (gate_closure_steps(df.index) if stage == "DA"
           else intraday_decision_steps(df.index, 0.5, DT))
    X = build_features(df, wq, dec, DT)
    rng = np.random.default_rng(1)
    for t in rng.choice(X.index.to_numpy(), size=8, replace=False):
        i = dec[t]
        j = i - 5
        bad_df = df.copy()
        bad_df.iloc[j + 1:] = 999.0
        need = int(vintage_for_lead((t - i) * DT))
        bad_w = wq.copy()
        for c in bad_w.columns:
            vintage = int(c.rsplit("|d", 1)[1])
            keep = np.zeros(len(bad_w), dtype=bool)
            if vintage == need:
                keep[t] = True
            bad_w.loc[~keep, c] = -777.0
        again = build_features(bad_df, bad_w, dec, DT)
        pd.testing.assert_series_equal(again.loc[t], X.loc[t], check_names=False)


def test_split_is_by_target_time_with_an_embargo_before_the_end():
    index = pd.date_range("2024-02-10", "2026-11-01", freq="h", tz="UTC")
    rows = pd.Index(np.arange(len(index)))
    train = split_rows(index, rows, "train")
    val = split_rows(index, rows, "validation")
    test = split_rows(index, rows, "test")
    assert not (train & val).any() and not (val & test).any()
    gap = pd.Timestamp(SPLITS["validation"][0], tz="UTC") - index[train].max()
    assert gap >= pd.Timedelta(hours=EMBARGO_H)
    assert index[test].min() >= pd.Timestamp(SPLITS["test"][0], tz="UTC")
    moved = split_rows(index, rows, "train", train_end=SPLITS["validation"][1])
    assert moved.sum() > train.sum()


# ------------------------------------------------------------------ the models
def test_rearrangement_removes_quantile_crossing():
    q = finish(np.array([[0.5, 0.3, 0.9, 1.2, -0.1]]))
    np.testing.assert_allclose(q, [[0.0, 0.3, 0.5, 0.9, 1.0]])


def synthetic_rows(n: int = 6000, seed: int = 0):
    """A target driven by one feature, with calendar columns the baselines use."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    x = rng.random(n)
    X = pd.DataFrame({c: rng.random(n) for c in
                      ("lead_h", "wind_on_power_proxy", "wind_on_ws_mean", "wind_off_power_proxy",
                       "ghi_mean", "cloud_mean", "temp_mean", "cos_zenith", "wind_cf_obs_1h",
                       "pv_cf_obs", "hour_sin", "hour_cos", "doy_sin", "doy_cos")})
    X["wind_cf_obs"] = x
    X["pv_cf_last_day"] = 0.0
    y = np.clip(0.2 + 0.6 * x + 0.05 * rng.standard_normal(n), 0, 1)
    return X, y, times


def coverage(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.mean(y[:, None] <= q, axis=0)


def pinball(q: np.ndarray, y: np.ndarray) -> float:
    d = y[:, None] - q
    return float(np.mean(np.maximum(LEVELS * d, (LEVELS - 1) * d)))


def test_climatology_is_calibrated_in_sample():
    X, y, times = synthetic_rows()
    q = Climatology().fit(X, y, times).predict(X, times)
    assert np.max(np.abs(coverage(q, y) - LEVELS)) < 0.03


def test_persistence_weights_wind_and_pv_by_capacity_share():
    X = pd.DataFrame({"wind_cf_obs": [0.6], "pv_cf_last_day": [0.3]})
    assert Persistence(2 / 3, 1 / 3).point(X)[0] == pytest.approx(0.5)


def test_linear_quantile_learns_a_linear_signal():
    pytest.importorskip("scipy")
    X, y, times = synthetic_rows()
    fit, held = slice(0, 4000), slice(4000, None)
    lin = LinearQuantile().fit(X.iloc[fit], y[fit], times[fit])
    clim = Climatology().fit(X.iloc[fit], y[fit], times[fit])
    q_lin = lin.predict(X.iloc[held], times[held])
    assert pinball(q_lin, y[held]) < 0.5 * pinball(clim.predict(X.iloc[held], times[held]), y[held])
    assert np.max(np.abs(coverage(q_lin, y[held]) - LEVELS)) < 0.05


def test_gradient_boosting_beats_climatology_on_a_learnable_signal():
    pytest.importorskip("lightgbm")
    X, y, times = synthetic_rows()
    fit, val, held = slice(0, 3000), slice(3000, 4500), slice(4500, None)
    gb = GradientBoosting(seed=0, n_jobs=1).fit(X.iloc[fit], y[fit], times[fit],
                                                X.iloc[val], y[val])
    clim = Climatology().fit(X.iloc[fit], y[fit], times[fit])
    q_gb = gb.predict(X.iloc[held], times[held])
    assert pinball(q_gb, y[held]) < 0.5 * pinball(clim.predict(X.iloc[held], times[held]), y[held])
    assert set(gb.best) == {f"{lv:.1f}" for lv in LEVELS}



def test_calibration_days_close_the_training_window_behind_an_embargo():
    from f2b.features import CALIBRATION_DAYS, fit_and_calibration_rows

    index = pd.date_range("2024-02-10", "2026-01-05", freq="h", tz="UTC")
    rows = pd.Index(np.arange(len(index)))
    fit, cal = fit_and_calibration_rows(index, rows)
    train = split_rows(index, rows, "train")
    assert not (fit & cal).any() and not ((fit | cal) & ~train).any()
    span = index[cal].max() - index[cal].min()
    assert abs(span - pd.Timedelta(days=CALIBRATION_DAYS)) < pd.Timedelta(hours=2)
    assert index[cal].min() - index[fit].max() >= pd.Timedelta(hours=EMBARGO_H)


class Biased:
    """A deliberately wrong quantile model: too narrow and centred too low."""
    name = "biased"

    def fit(self, X, y, times, X_val=None, y_val=None):
        return self

    def predict(self, X, times):
        x = X["wind_cf_obs"].to_numpy()
        return finish((0.2 + 0.6 * x - 0.05)[:, None] + 0.01 * (LEVELS - 0.5)[None, :])


def test_recalibration_restores_nominal_coverage_out_of_sample():
    from f2b.models import Recalibrated

    X, y, times = synthetic_rows(n=9000)
    cal, held = slice(0, 4500), slice(4500, None)
    raw = Biased().predict(X.iloc[held], times[held])
    assert np.max(np.abs(coverage(raw, y[held]) - LEVELS)) > 0.3
    model = Recalibrated(Biased()).fit(X.iloc[:0], y[:0], times[:0],
                                       X.iloc[cal], y[cal], times[cal])
    fixed = model.predict(X.iloc[held], times[held])
    assert np.max(np.abs(coverage(fixed, y[held]) - LEVELS)) < 0.03

"""Smoke tests for experiment runner modules."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from prosumer.experiments.resolution_study import _hourly_frame, _hourly_mean_per_quarter
from prosumer.reporting import figures


def test_figures_generation(tmp_path):
    reports = Path(__file__).parents[1] / "reports"
    if (reports / "household_sweep.csv").exists() and (reports / "rolling_eval_htw_H28").exists():
        figs = figures.make_all(reports=reports, out=tmp_path)
        assert len(figs) == 4
        for f in figs:
            assert f.exists()
            assert f.stat().st_size > 1000


def test_hourly_mean_per_quarter_consistency():
    idx = pd.date_range("2025-10-01", periods=16, freq="15min", tz="UTC")
    spot = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.5, 0.5, 0.5] * 2, index=idx)
    mean_series = _hourly_mean_per_quarter(spot)
    assert len(mean_series) == 16
    assert mean_series.iloc[0] == pytest.approx(0.25)
    assert mean_series.iloc[3] == pytest.approx(0.25)
    assert mean_series.iloc[4] == pytest.approx(0.50)


def test_hourly_frame_resampling():
    idx = pd.date_range("2025-10-01", periods=8, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "load_kw": [1.0, 2.0, 3.0, 4.0, 2.0, 2.0, 2.0, 2.0],
        "pv_kw": [0.0] * 8,
        "spot_eur_per_kwh": [0.1] * 8,
    }, index=idx)
    h_df = _hourly_frame(df)
    assert len(h_df) == 2
    assert h_df["load_kw"].iloc[0] == pytest.approx(2.5)
    assert h_df["load_kw"].iloc[1] == pytest.approx(2.0)


def test_dataset_save_load_roundtrip(tmp_path):
    from prosumer.data.dataset import load_dataset, save_dataset
    idx = pd.date_range("2025-01-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"load_kw": [1.0, 2.0, 3.0, 4.0]}, index=idx)
    p = save_dataset(df, tmp_path / "test.parquet")
    loaded = load_dataset(p)
    pd.testing.assert_frame_equal(df, loaded, check_freq=False)


def test_rl_eval_split_and_env():
    from prosumer.experiments.rl_eval import _split
    idx = pd.date_range("2024-06-01", periods=96 * 4, freq="15min", tz="Europe/Berlin").tz_convert("UTC")
    df = pd.DataFrame({"load_kw": np.ones(len(idx))}, index=idx)
    tr, te = _split(df, train=("2024-06-01", "2024-06-03"), test_start="2024-06-03")
    assert len(tr) == 96 * 2
    assert len(te) == 96 * 2


def test_household_sweep_driver(tmp_path, monkeypatch):
    import prosumer.experiments.household_sweep as hs
    monkeypatch.setattr(hs, "_one", lambda h, d, p: {
        "household": h, "peak_kw": 5.0, "cost_b1": 100.0, "cost_b2": 50.0,
        "cost_b3": 55.0, "capture_b3_pct": 90.0, "violations_b3": 0
    })
    out = tmp_path / "sweep.csv"
    res = hs.run_household_sweep(["H01", "H02"], "dummy_d", "dummy_h", str(out), workers=1)
    assert len(res) == 2
    assert set(res["household"]) == {"H01", "H02"}

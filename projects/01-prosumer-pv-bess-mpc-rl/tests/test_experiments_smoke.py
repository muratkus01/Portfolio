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
        assert len(figs) in (3, 4)
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


def _fake_household(h, d, p):
    return {"household": h, "peak_kw": 5.0, "cost_b1": 100.0, "cost_b2": 50.0,
            "cost_b3": 55.0, "capture_b3_pct": 90.0, "violations_b3": 0}


def _sweep_inputs(tmp_path):
    """Two small stand-in files: the sweep fingerprints its inputs, so they must exist."""
    d, h = tmp_path / "site.parquet", tmp_path / "htw.parquet"
    d.write_bytes(b"dataset v1")
    h.write_bytes(b"htw households")
    return str(d), str(h)


def test_household_sweep_driver(tmp_path, monkeypatch):
    import prosumer.experiments.household_sweep as hs
    monkeypatch.setattr(hs, "_one", _fake_household)
    d, h = _sweep_inputs(tmp_path)
    out = tmp_path / "sweep.csv"
    res = hs.run_household_sweep(["H01", "H02"], d, h, str(out), workers=1)
    assert len(res) == 2
    assert set(res["household"]) == {"H01", "H02"}


def test_household_sweep_resumes_only_the_missing_households_of_the_same_inputs(tmp_path, monkeypatch):
    import prosumer.experiments.household_sweep as hs
    calls = []
    monkeypatch.setattr(hs, "_one", lambda h, d, p: calls.append(h) or _fake_household(h, d, p))
    d, h = _sweep_inputs(tmp_path)
    out = tmp_path / "sweep.csv"
    hs.run_household_sweep(["H01"], d, h, str(out), workers=1)
    res = hs.run_household_sweep(["H01", "H02"], d, h, str(out), workers=1)
    assert calls == ["H01", "H02"]
    assert list(res["household"]) == ["H01", "H02"]


def test_household_sweep_refuses_to_resume_from_changed_inputs(tmp_path, monkeypatch):
    """After the HTW clock fix the rerun found every household done, exited 0 and kept the stale rows."""
    import prosumer.experiments.household_sweep as hs
    monkeypatch.setattr(hs, "_one", _fake_household)
    d, h = _sweep_inputs(tmp_path)
    out = tmp_path / "sweep.csv"
    hs.run_household_sweep(["H01", "H02"], d, h, str(out), workers=1)
    Path(d).write_bytes(b"dataset v2")
    with pytest.raises(ValueError, match="not written from these inputs"):
        hs.run_household_sweep(["H01", "H02"], d, h, str(out), workers=1)


def test_household_sweep_refuses_to_resume_a_csv_without_input_fingerprint(tmp_path, monkeypatch):
    import prosumer.experiments.household_sweep as hs
    monkeypatch.setattr(hs, "_one", _fake_household)
    d, h = _sweep_inputs(tmp_path)
    out = tmp_path / "sweep.csv"
    pd.DataFrame([_fake_household("H01", d, h)]).to_csv(out, index=False)
    with pytest.raises(ValueError, match="not written from these inputs"):
        hs.run_household_sweep(["H01", "H02"], d, h, str(out), workers=1)

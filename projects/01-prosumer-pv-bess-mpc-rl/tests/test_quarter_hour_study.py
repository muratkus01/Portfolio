"""Quarter-hour study: the perfect-foresight value of the finer price signal is never negative."""
from __future__ import annotations

import numpy as np
import pandas as pd

from prosumer.experiments.resolution_study import _hourly_mean_per_quarter, run_resolution_study


def _synthetic(days=30, seed=3):
    idx = pd.date_range("2025-09-01", periods=days * 96, freq="15min",
                        tz="Europe/Berlin").tz_convert("UTC")
    rng = np.random.default_rng(seed)
    h = (np.arange(len(idx)) % 96) / 4
    pv = np.clip(5 * np.sin(np.pi * (h - 6) / 14), 0, None) * rng.uniform(0.5, 1, len(idx))
    load = 0.3 + 0.4 * ((h > 17) & (h < 22)) + 0.1 * rng.random(len(idx))
    spot = 0.08 + 0.05 * np.cos(2 * np.pi * (h - 19) / 24) + 0.03 * rng.standard_normal(len(idx))
    return pd.DataFrame({"load_kw": load, "pv_kw": pv, "pv_fc_kw": pv,
                         "spot_eur_per_kwh": spot}, index=idx)


def test_hourly_mean_is_constant_within_each_hour():
    df = _synthetic(days=2)
    m = _hourly_mean_per_quarter(df["spot_eur_per_kwh"]).to_numpy()
    assert np.allclose(m.reshape(-1, 4).std(axis=1), 0)
    assert np.isclose(m.mean(), df["spot_eur_per_kwh"].mean())


def test_value_of_quarter_hour_signal_for_perfect_foresight_is_non_negative():
    df = _synthetic()
    res = run_resolution_study(df, start="2025-09-20", train=("2025-09-01", "2025-09-20"),
                               progress=False)
    s = res["summary"]
    assert s.loc["B2", "value of 15-min signal EUR"] >= -1e-6
    assert s.loc["B1", "value of 15-min signal EUR"] == 0.0

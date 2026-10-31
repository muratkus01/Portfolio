"""Quarter-hour study: the perfect-foresight value of the finer price signal is never negative,
and its yearly value is scaled by the days the study covers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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


@pytest.fixture(scope="module")
def summary() -> pd.DataFrame:
    """The study on 2025-09-20 to 2025-09-30: 11 days, all inside one calendar month."""
    res = run_resolution_study(_synthetic(), start="2025-09-20",
                               train=("2025-09-01", "2025-09-20"), progress=False)
    return res["summary"]


def test_hourly_mean_is_constant_within_each_hour():
    df = _synthetic(days=2)
    m = _hourly_mean_per_quarter(df["spot_eur_per_kwh"]).to_numpy()
    assert np.allclose(m.reshape(-1, 4).std(axis=1), 0)
    assert np.isclose(m.mean(), df["spot_eur_per_kwh"].mean())


def test_value_of_quarter_hour_signal_for_perfect_foresight_is_non_negative(summary):
    assert summary.loc["B2", "value of 15-min signal EUR"] >= -1e-6
    assert summary.loc["B1", "value of 15-min signal EUR"] == 0.0


def test_yearly_value_is_scaled_by_the_days_covered_not_by_calendar_months(summary):
    """11 days are 11/365 of a year, not one twelfth.

    Regression: the yearly column was value * 12 / (number of months), so a partial last
    month counted as a whole one. The published run ends on 2026-09-17, and its "per year"
    figure was the 352-day total.
    """
    value = summary.loc["B2", "value of 15-min signal EUR"]
    assert value > 0.01                                   # otherwise the check below is empty
    assert summary.loc["B2", "value of 15-min signal EUR/yr"] == pytest.approx(value * 365 / 11)

"""The value-of-forecast table: block-bootstrap interval and capture ratio."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from f2b.evaluate import value_of_forecast


def test_value_of_forecast_interval_contains_the_point_estimate():
    days = pd.Index(pd.date_range("2025-07-01", periods=60, freq="D").date)
    rng = np.random.default_rng(3)
    base = rng.normal(1000, 200, 60)
    daily = pd.DataFrame({("F0 climatology", "B1"): base,
                          ("F3 seed 0", "B1"): base + 50 + rng.normal(0, 30, 60),
                          ("perfect", "REF zero imbalance"): base + 120}, index=days)
    row = value_of_forecast(daily).iloc[0]
    assert row["ci90_low"] < row["gain_vs_F0_eur"] < row["ci90_high"]
    assert row["gain_vs_F0_eur"] == pytest.approx(3000, rel=0.15)
    assert 0.2 < row["capture_of_F0_to_REF"] < 0.6

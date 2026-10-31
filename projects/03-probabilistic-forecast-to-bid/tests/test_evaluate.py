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


def test_a_cached_ladder_round_trips_exactly(tmp_path):
    from f2b.evaluate import Ladder, _stage, load_ladder, save_ladder
    from f2b.models import LEVELS

    rng = np.random.default_rng(0)
    n = 50
    decision = np.arange(n) - 4
    rows = np.arange(10, 40)
    q = np.sort(rng.random((len(rows), len(LEVELS))), axis=1) * 300
    ladder = Ladder({"F0 climatology": [_stage("DA", decision, rows, q, n)]}, {})
    save_ladder(ladder, tmp_path / "ladder.npz")
    back = load_ladder(tmp_path / "ladder.npz", n)
    a, b = ladder.stages["F0 climatology"][0], back.stages["F0 climatology"][0]
    np.testing.assert_array_equal(a.quantiles, b.quantiles)
    np.testing.assert_array_equal(a.issue, b.issue)
    np.testing.assert_array_equal(a.decision, b.decision)


def market_frame(n: int = 400, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2025-07-01", periods=n, freq="15min", tz="UTC")
    da = 80 + 20 * rng.standard_normal(n)
    return pd.DataFrame({"truth_mw": 100 + 30 * rng.random(n), "price_da": da,
                         "rebap": da + 60 * rng.standard_normal(n), "rebap_long": np.nan,
                         "id_aep": da + 5 * rng.standard_normal(n)}, index=index)


def test_error_alignment_splits_b0_against_ref_exactly():
    from f2b.analysis import error_alignment
    from f2b.config import RunConfig
    from f2b.evaluate import Ladder, _stage, daily_net
    from f2b.models import LEVELS

    df = market_frame()
    n = len(df)
    rng = np.random.default_rng(2)
    median = df["truth_mw"].to_numpy() + 8 + 10 * rng.standard_normal(n)
    q = median[:, None] + np.linspace(-10, 10, len(LEVELS))[None, :]
    rows = np.arange(n)
    ladder = Ladder({"F9 test": [_stage("DA", rows, rows, q, n)]}, {})
    mask = np.ones(n, dtype=bool)
    run = RunConfig()
    row = error_alignment(ladder, df, mask, run).iloc[0]
    assert row["bias_part_eur"] + row["alignment_part_eur"] == pytest.approx(
        row["b0_minus_ref_eur"])
    totals, _ = daily_net(ladder, df, mask, run, {"F9 test": 0.25}, 0.5)
    net = totals.set_index(["rung", "policy"])["net_revenue"]
    assert net[("F9 test", "B0 day-ahead only")] - net[("perfect", "REF zero imbalance")] == (
        pytest.approx(row["b0_minus_ref_eur"]))


def test_quality_vs_value_is_plus_one_when_better_forecasts_earn_more():
    from f2b.analysis import quality_vs_value

    scores = pd.DataFrame({"rung": ["F0", "F1", "F3 seed 0", "F3 seed 1"],
                           "stage": "DA", "pinball": [8.0, 5.0, 2.0, 2.1]})
    totals = pd.DataFrame({"rung": ["F0", "F1", "F3 seed 0", "F3 seed 1"],
                           "policy": "B0", "net_revenue": [1.0, 2.0, 3.0, 0.0]})
    row = quality_vs_value(scores, totals).iloc[0]
    assert row["rungs"] == 3                       # the second seed does not vote
    assert row["spearman_skill_vs_eur"] == pytest.approx(1.0)

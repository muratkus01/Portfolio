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


def test_intraday_trades_and_imbalances_settle_at_the_published_prices():
    """Fix 1: an intraday trade clears at the ID AEP, at the day-ahead price only where the AEP
    is missing, and a deviation at the published reBAP. The first version priced intraday
    trades at the day-ahead price and deviations at a synthetic series."""
    from f2b.config import RunConfig
    from f2b.evaluate import Ladder, _stage, daily_net
    from f2b.models import LEVELS
    from f2b.policies import b1_point_intraday

    df = market_frame()
    n = len(df)
    rng = np.random.default_rng(4)
    truth = df["truth_mw"].to_numpy()
    band = np.linspace(-10, 10, len(LEVELS))[None, :]
    rows = np.arange(n)
    stages = [_stage("DA", rows, rows, (truth + 8 + 10 * rng.standard_normal(n))[:, None] + band, n),
              _stage("ID-2h", rows, rows, (truth + 3 + 2 * rng.standard_normal(n))[:, None] + band, n)]
    ladder = Ladder({"F9 test": stages}, {})
    run = RunConfig()
    position, trades = b1_point_intraday(stages)
    traded = sum(t.sum() for t in trades) * run.dt
    deviation = (truth - position).sum() * run.dt
    assert abs(traded) > 100 and abs(deviation) > 100  # both legs carry volume

    def b1(frame: pd.DataFrame) -> pd.Series:
        totals, _ = daily_net(ladder, frame, np.ones(n, dtype=bool), run, {"F9 test": 0.25}, 0.5)
        return totals.set_index(["rung", "policy"]).loc[("F9 test", "B1 intraday to median")]

    base = b1(df)
    moved_aep = b1(df.assign(id_aep=df["id_aep"] + 10.0))
    assert moved_aep["id_revenue"] - base["id_revenue"] == pytest.approx(10.0 * traded)
    moved_rebap = b1(df.assign(rebap=df["rebap"] + 10.0))
    assert moved_rebap["imbalance_settlement"] - base["imbalance_settlement"] == pytest.approx(
        10.0 * deviation)
    no_aep = b1(df.assign(id_aep=np.nan))
    assert no_aep["id_revenue"] == pytest.approx(
        sum((t * df["price_da"].to_numpy()).sum() for t in trades) * run.dt)


@pytest.mark.parametrize("phase, first_scored", [("validation", "2025-07-02"),
                                                 ("test", "2026-01-01")])
def test_learned_rungs_never_fit_stop_or_calibrate_on_the_scored_window(monkeypatch, phase,
                                                                       first_scored):
    """Fix 10: every rung is fitted, LightGBM stopped early and each quantile recalibrated only
    on days before the window that is scored. LightGBM once stopped early on the validation
    window itself. The model classes are replaced by a spy, so only the wiring is tested."""
    import f2b.evaluate as ev
    from f2b.config import RunConfig
    from f2b.models import LEVELS

    index = pd.date_range("2024-02-18", "2026-10-15", freq="15min", tz="UTC")
    n = len(index)
    df = pd.DataFrame({"truth_mw": np.full(n, 100.0), "tso_da_mw": 100.0}, index=index)
    seen = {"fit": [], "early stopping": [], "predict": []}

    class Spy:
        name, best = "spy", {}

        def __init__(self, *args, **kwargs):
            pass

        def fit(self, X, y, times, X_val=None, y_val=None):
            seen["fit"].append(times.max())
            if X_val is not None:
                seen["early stopping"].append(index[X_val.index].max())
            return self

        def predict(self, X, times):
            seen["predict"].append((times.min(), times.max()))
            return np.zeros((len(X), len(LEVELS)))

    for name in ("Climatology", "Persistence", "TsoForecast", "LinearQuantile",
                 "GradientBoosting"):
        monkeypatch.setattr(ev, name, Spy)
    monkeypatch.setattr(ev, "build_features", lambda frame, weather_q, decision, dt: (
        pd.DataFrame({"x": np.zeros(len(frame))})))
    monkeypatch.setattr(ev, "stage_decisions", lambda idx, run: {"DA": np.arange(len(idx))})

    ev.fit_ladder(df, None, RunConfig(), phase, seeds=(0,), log=lambda *a, **k: None)

    first = pd.Timestamp(first_scored, tz="UTC")
    assert seen["fit"] and seen["early stopping"]
    assert max(seen["fit"]) < first
    assert max(seen["early stopping"]) < first
    calibrated = [hi for lo, hi in seen["predict"] if hi < first]
    scored = [lo for lo, hi in seen["predict"] if hi >= first]
    assert calibrated, "the learned rungs predicted no held-out rows to recalibrate on"
    assert scored and min(scored) >= first

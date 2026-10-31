"""Forecast store integrity, decision timing, scoring, and settlement identities."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from f2b.config import MarketConfig, RunConfig
from f2b.forecast import (
    ForecastStore,
    build_store,
    pinball_loss,
    score_stage,
    stages_from_store,
)
from f2b.policies import (
    b0_day_ahead_only,
    b1_point_intraday,
    b2_perfect_speculation,
    b2_zero_imbalance,
    b3_deadband,
    newsvendor_quantile,
    nv_policy,
    rebap_series,
    settle,
)
from f2b.scipy_stub import norm_ppf
from f2b.stages import (
    Stage,
    audit_stages,
    gate_closure_steps,
    intraday_decision_steps,
    valid_steps,
)

CAP = 300.0
N = 1500
INDEX = pd.date_range("2025-03-01", periods=N, freq="15min", tz="UTC")


def truth_series(n: int = N) -> np.ndarray:
    """Synthetic portfolio output. DETERMINISTIC - the same series on every call.

    Two things this function must get right, both learned the hard way:

    1. **A fresh seeded generator, not a module-level one.** With a shared stateful RNG each
       call returns a *different* realisation, so a forecast store built from one call was
       being scored against another - which looks exactly like a badly miscalibrated model
       and is impossible to debug from the outside. Any test fixture that generates "the
       truth" must be reproducible.

    2. **No saturation at the capacity bound.** If the series frequently sits at 0 or at
       capacity, the predictive distribution is truncated there and reliability degrades for
       reasons unrelated to the forecast model. Keeping the peak below CAP isolates the
       property under test.
    """
    rng = np.random.default_rng(20240811)
    h = np.arange(n) * 0.25 % 24
    wind = np.clip(100 + 55 * np.sin(np.arange(n) / 190) + 15 * rng.standard_normal(n), 5, 170)
    pv = 85 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1) * np.clip(1 - 0.35 * rng.random(n), 0, 1)
    return np.clip(wind + pv, 0, CAP)


def make_store(truth: np.ndarray, seed: int = 1) -> ForecastStore:
    run = RunConfig()
    return build_store(truth, run.forecast, run.dt, 144, np.random.default_rng(seed), CAP)


@pytest.fixture(scope="module")
def store():
    return make_store(truth_series())


@pytest.fixture(scope="module")
def stages(store):
    return stages_from_store(store, INDEX, RunConfig())


# ------------------------------------------------------------------ the store
def test_store_refuses_lookahead(store):
    """The architectural guarantee: a forecast for the past cannot be requested."""
    with pytest.raises(ValueError):
        store.at(100, 50)
    store.at(100, 150)          # must not raise


def test_store_refuses_a_lead_beyond_its_horizon(store):
    """Regression: a lead past `max_lead` used to return the forecast of ANOTHER target.

    `at(0, 200)` silently answered with the forecast for step 143, issued at 0.
    """
    with pytest.raises(ValueError):
        store.at(0, store.max_lead)


# ------------------------------------------------------------------ decision times
@pytest.mark.parametrize("target_utc, gate_utc", [
    ("2025-01-15 09:00", "2025-01-14 11:00"),    # winter: 12:00 CET is 11:00 UTC
    ("2025-07-15 09:00", "2025-07-14 10:00"),    # summer: 12:00 CEST is 10:00 UTC
    ("2025-03-30 12:00", "2025-03-29 11:00"),    # delivery day of the spring switch
    ("2025-03-31 05:00", "2025-03-30 10:00"),    # gate on the spring switch day
    ("2025-10-26 12:00", "2025-10-25 10:00"),    # delivery day of the autumn switch
    ("2025-10-27 05:00", "2025-10-26 11:00"),    # gate on the autumn switch day
    ("2025-01-15 22:45", "2025-01-14 11:00"),    # 23:45 CET is still the 15th
    ("2025-01-15 23:15", "2025-01-15 11:00"),    # 00:15 CET belongs to the 16th
])
def test_gate_closure_is_noon_berlin_on_the_day_before(target_utc, gate_utc):
    index = pd.date_range("2024-12-25", "2025-11-05", freq="15min", tz="UTC")
    t = index.get_loc(pd.Timestamp(target_utc, tz="UTC"))
    assert index[gate_closure_steps(index)[t]] == pd.Timestamp(gate_utc, tz="UTC")


def test_intraday_stage_waits_for_the_market_to_open():
    """12 h before 00:00 local is 12:00 local, before the 16:00 opening: decide at opening."""
    index = pd.date_range("2025-05-30", "2025-06-03", freq="15min", tz="UTC")
    t = index.get_loc(pd.Timestamp("2025-06-01 22:00", tz="UTC"))    # 00:00 CEST, 2 June
    dec = intraday_decision_steps(index, 12.0, 0.25)[t]
    assert index[dec] == pd.Timestamp("2025-06-01 14:00", tz="UTC")   # 16:00 CEST, 1 June
    late = index.get_loc(pd.Timestamp("2025-06-02 20:00", tz="UTC"))
    assert index[intraday_decision_steps(index, 2.0, 0.25)[late]] == pd.Timestamp(
        "2025-06-02 18:00", tz="UTC")


def poisoned_after_decision(store: ForecastStore, decision: np.ndarray) -> ForecastStore:
    """A copy of the store with every forecast issued after its target's decision set to NaN."""
    n, max_lead = store.mean.shape
    issue = np.arange(n)[:, None]
    target = issue + np.arange(max_lead)[None, :]
    inside = target < n
    late = np.zeros_like(inside)
    late[inside] = issue.repeat(max_lead, 1)[inside] > decision[target[inside]]
    mean, q = store.mean.copy(), store.quantiles.copy()
    mean[late] = np.nan
    q[late] = np.nan
    return ForecastStore(mean=mean, quantiles=q, levels=store.levels, max_lead=store.max_lead)


def test_every_stage_uses_only_forecasts_issued_by_its_decision_time(store, stages):
    """Regression: the day-ahead leg used a fixed 24 h lead, so for delivery after 12:00 it
    read a forecast issued after the 12:00 gate closure. Poisoning everything issued after
    each stage's decision must leave that stage's forecasts untouched."""
    ok = valid_steps(stages)
    assert ok.sum() > N // 2
    for st in stages:
        clean = poisoned_after_decision(store, st.decision)
        again = next(s for s in stages_from_store(clean, INDEX, RunConfig()) if s.name == st.name)
        assert np.array_equal(again.mean[ok], st.mean[ok])
        assert not np.isnan(again.quantiles[ok]).any()


def test_audit_counts_a_stage_that_reads_after_its_decision(stages):
    """The audit must be able to fail. The earlier one only re-checked the store's own
    target-before-issue guard, so a policy reading late forecasts still reported 0."""
    assert audit_stages(stages) == 0
    st = stages[0]
    cheat = Stage(name="cheat", issue=np.where(st.decision >= 0, st.decision + 1, -1),
                  decision=st.decision, mean=st.mean, quantiles=st.quantiles, levels=st.levels)
    assert audit_stages([cheat]) == int((st.decision >= 0).sum())


# ------------------------------------------------------------------ the synthetic ablation
def test_forecast_revisions_carry_information(store):
    """Regression: errors for the same target at different leads were independent, so every
    revision was fresh noise. They must be nested: strongly correlated, shrinking toward
    delivery."""
    truth = truth_series()
    t = np.arange(200, N)
    e_far = store.mean[t - 48, 48] - truth[t]
    e_near = store.mean[t - 2, 2] - truth[t]
    assert np.corrcoef(e_far, e_near)[0, 1] > 0.3
    assert np.mean(np.abs(e_near)) < np.mean(np.abs(e_far))


def test_published_spread_ignores_the_future():
    """Regression: the spread used a centred window, i.e. truth up to 2 h after the issue."""
    truth = truth_series()
    later = truth.copy()
    j = 800
    later[j + 1:] = np.clip(later[j + 1:] * 1.7 + 20, 0, 290)
    a, b = make_store(truth), make_store(later)
    issues = np.arange(600, j + 1)
    for k in (1, 4):
        qa, qb = a.quantiles[issues, k], b.quantiles[issues, k]
        unclipped = ((qa[:, 0] > 0) & (qa[:, -1] < CAP) & (qb[:, 0] > 0) & (qb[:, -1] < CAP)
                     & (issues + k <= j))
        assert unclipped.sum() > 50
        np.testing.assert_allclose((qa[:, -1] - qa[:, 0])[unclipped],
                                   (qb[:, -1] - qb[:, 0])[unclipped])


def test_quantiles_are_monotone(store):
    q = store.quantiles[np.arange(0, N - 144, 37)]
    assert np.all(np.diff(q, axis=-1) >= -1e-9), "quantile crossing detected"


def test_error_shrinks_toward_delivery(stages):
    truth = truth_series()
    ok = valid_steps(stages)
    da = score_stage(stages[0], truth, ok, 0.25)
    last = score_stage(stages[-1], truth, ok, 0.25)
    assert last["MAE"] < da["MAE"]
    assert last["pinball"] < da["pinball"]


def test_forecast_is_reasonably_calibrated(stages):
    """Close to delivery the ablation is calibrated; at day-ahead clipping to [0, capacity]
    truncates part of the wide distribution, so the bound is looser there."""
    truth = truth_series()
    ok = valid_steps(stages)
    assert score_stage(stages[-1], truth, ok, 0.25)["calibration_error"] < 0.05
    assert score_stage(stages[0], truth, ok, 0.25)["calibration_error"] < 0.10


# ------------------------------------------------------------------ scoring
def test_pinball_matches_hand_computation():
    levels = np.array([0.25, 0.75])
    # y = 1 against quantiles 0 and 2: losses 0.25 * 1 and 0.25 * 1, mean 0.25
    assert pinball_loss(np.array([1.0]), np.array([[0.0, 2.0]]), levels) == pytest.approx(0.25)


def test_perfect_forecast_scores_zero():
    y = np.array([5.0, 5.0])
    levels = np.linspace(0.1, 0.9, 9)
    q = np.full((2, 9), 5.0)
    assert pinball_loss(y, q, levels) == pytest.approx(0.0)


def test_norm_ppf_matches_known_values():
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert norm_ppf(0.025) == pytest.approx(-1.959964, abs=1e-5)
    assert norm_ppf(0.001) == pytest.approx(-3.090232, abs=1e-4)


# ------------------------------------------------------------------ decision layer
def two_stage(da_mean, id_mean, id_spread) -> list[Stage]:
    """Hand-built day-ahead and one intraday stage with symmetric quantiles."""
    n = len(da_mean)
    levels = np.linspace(0.1, 0.9, 9)
    offsets = np.linspace(-0.5, 0.5, 9)

    def stage(name, m, spread):
        m = np.asarray(m, float)
        q = m[:, None] + np.asarray(spread, float)[:, None] * offsets[None, :]
        return Stage(name=name, issue=np.zeros(n, int), decision=np.zeros(n, int),
                     mean=m, quantiles=q, levels=levels)

    return [stage("DA", da_mean, np.full(n, 20.0)), stage("ID", id_mean, id_spread)]


def test_deadband_trades_only_the_excess_over_the_band():
    """B3's mechanism, pinned exactly: with k = 0.25 and a spread of 40 the band is 10."""
    st = two_stage([100.0, 100.0, 100.0], [104.0, 130.0, 70.0], [40.0, 40.0, 40.0])
    pos, trades = b3_deadband(st, k=0.25)
    np.testing.assert_allclose(trades[0], [0.0, 20.0, -20.0])
    np.testing.assert_allclose(pos, [100.0, 120.0, 80.0])


def test_deadband_without_a_band_is_b1(stages):
    ok = valid_steps(stages)
    pos3, tr3 = b3_deadband(stages, k=0.0)
    pos1, tr1 = b1_point_intraday(stages)
    np.testing.assert_allclose(pos3[ok], pos1[ok])
    for a, b in zip(tr3, tr1):
        np.testing.assert_allclose(a[ok], b[ok])


def test_deadband_trades_less_than_b1(stages):
    ok = valid_steps(stages)
    _, tr3 = b3_deadband(stages, k=0.25)
    _, tr1 = b1_point_intraday(stages)
    assert sum(np.abs(t[ok]).sum() for t in tr3) < sum(np.abs(t[ok]).sum() for t in tr1)


def test_newsvendor_fractile():
    assert newsvendor_quantile(30.0, 10.0) == pytest.approx(0.75)
    assert newsvendor_quantile(10.0, 10.0) == pytest.approx(0.5)


def test_newsvendor_policy_reads_the_requested_quantile():
    st = two_stage([100.0], [110.0], [40.0])
    pos, trades = nv_policy(st, 0.9)
    assert pos[0] == pytest.approx(110.0 + 0.5 * 40.0)
    assert trades[0][0] == pytest.approx(pos[0] - (100.0 + 0.5 * 20.0))


def test_settlement_identity():
    """Committing the truth exactly must earn precisely the day-ahead value of production."""
    run = RunConfig()
    truth = truth_series(400)
    price = np.full(400, 70.0)
    rebap = np.full(400, 120.0)
    pos, trades = b2_zero_imbalance(truth)
    r = settle(pos, trades, truth, price, rebap, price, run)
    assert r["imbalance_mwh"] == pytest.approx(0.0)
    assert r["net_revenue"] == pytest.approx(np.sum(truth * price) * run.dt)


def test_imbalance_settles_each_direction_at_its_published_price():
    """Short pays reBAP; long is paid the long-side value where the two differ."""
    run = RunConfig()
    truth = np.array([10.0, 10.0])
    pos = np.array([12.0, 8.0])                 # short 2 MW, then long 2 MW
    rebap_short, rebap_long = np.array([200.0, 200.0]), np.array([50.0, 50.0])
    r = settle(pos, [], truth, np.zeros(2), rebap_short, np.zeros(2), run,
               rebap_long=rebap_long)
    assert r["imbalance_settlement"] == pytest.approx((-2 * 200.0 + 2 * 50.0) * run.dt)


def test_speculation_ceiling_dominates_zero_imbalance():
    """The information ceiling must beat the zero-imbalance reference, by construction."""
    run = RunConfig()
    truth = truth_series(600)
    rng = np.random.default_rng(7)
    price = 70 + 20 * rng.standard_normal(600)
    rebap = rebap_series(600, price, np.diff(truth, prepend=truth[0]),
                         run.market, np.random.default_rng(2))
    ref = settle(*b2_zero_imbalance(truth), truth, price, rebap, price, run)
    ceil = settle(*b2_perfect_speculation(truth, price, rebap, CAP),
                  truth, price, rebap, price, run)
    assert ceil["net_revenue"] >= ref["net_revenue"] - 1e-6


def test_intraday_correction_reduces_imbalance(stages):
    run = RunConfig()
    truth = truth_series()
    ok = valid_steps(stages)
    price = np.full(ok.sum(), 75.0)
    rebap = np.full(ok.sum(), 130.0)

    def run_policy(pos, trades):
        return settle(pos[ok], [t[ok] for t in trades], truth[ok], price, rebap, price, run)

    b0 = run_policy(*b0_day_ahead_only(stages))
    b1 = run_policy(*b1_point_intraday(stages))
    assert b1["imbalance_mwh"] < b0["imbalance_mwh"]


def test_rebap_is_heavy_tailed():
    """A Gaussian imbalance price would remove the feature the synthetic ablation is about."""
    price = np.full(20000, 70.0)
    signal = np.random.default_rng(5).standard_normal(20000)
    r = rebap_series(20000, price, signal, MarketConfig(), np.random.default_rng(4))
    z = (r - r.mean()) / r.std()
    assert float(np.mean(z ** 4)) > 3.5, "excess kurtosis expected relative to a normal"

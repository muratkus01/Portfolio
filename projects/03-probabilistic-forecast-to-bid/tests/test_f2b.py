"""Forecast store integrity, scoring, and settlement identities."""
from __future__ import annotations

import numpy as np
import pytest

from f2b.config import ForecastConfig, MarketConfig, RunConfig
from f2b.forecast import (audit_no_lookahead, build_store, calibration_error,
                          crps_from_quantiles, pinball_loss, score_store)
from f2b.policies import (b0_day_ahead_only, b1_point_intraday, b2_perfect_speculation,
                          b2_zero_imbalance, b3_stochastic_mpc, imbalance_cost_asymmetry,
                          newsvendor_quantile, nv_policy, rebap_series, settle)
from f2b.scipy_stub import norm_ppf

RNG = np.random.default_rng(0)
CAP = 300.0


def truth_series(n: int = 1500) -> np.ndarray:
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


@pytest.fixture(scope="module")
def store():
    run = RunConfig()
    return build_store(truth_series(), run.forecast, run.dt, 144,
                       np.random.default_rng(1), CAP)


# ------------------------------------------------------------------ no look-ahead
def test_store_refuses_lookahead(store):
    """The architectural guarantee: a forecast for the past cannot be requested."""
    with pytest.raises(ValueError):
        store.at(100, 50)
    store.at(100, 150)          # must not raise


def test_lookahead_audit_is_clean(store):
    assert audit_no_lookahead(store, 1500, sample=400) == 0


# ------------------------------------------------------------------ forecast quality
def test_error_grows_with_lead_time(store):
    truth = truth_series()
    short = score_store(store, truth, 1)
    long_ = score_store(store, truth, 96)
    assert long_["MAE"] > short["MAE"]
    assert long_["CRPS"] > short["CRPS"]


def test_forecast_is_reasonably_calibrated(store):
    """Calibration must be good at short leads and degrade gracefully, not catastrophically.

    The threshold loosens with lead time for a real reason: the predictive distribution is
    clipped to the physical range [0, capacity], and at long leads the spread is wide enough
    that clipping truncates a visible share of the mass. A truncated Gaussian is no longer
    calibrated at its extreme quantiles.

    This is a genuine property of the generator, not a tolerance fudge. The fix in a
    production model is to predict in a transformed space (logit of capacity factor) so the
    physical bounds are respected without truncation - the documented upgrade, and the reason
    the bounds below are asserted rather than removed.
    """
    truth = truth_series()
    assert score_store(store, truth, 1)["calibration_error"] < 0.05
    assert score_store(store, truth, 24)["calibration_error"] < 0.07
    assert score_store(store, truth, 96)["calibration_error"] < 0.10


def test_quantiles_are_monotone(store):
    q = store.quantiles[np.arange(0, 1500, 37)]
    assert np.all(np.diff(q, axis=-1) >= -1e-9), "quantile crossing detected"


def test_crps_is_twice_pinball():
    y = np.array([1.0, 2.0, 3.0])
    levels = np.linspace(0.1, 0.9, 9)
    q = np.tile(np.linspace(0.5, 3.5, 9), (3, 1))
    assert crps_from_quantiles(y, q, levels) == pytest.approx(2 * pinball_loss(y, q, levels))


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
def test_newsvendor_fractile():
    assert newsvendor_quantile(30.0, 10.0) == pytest.approx(0.75)
    assert newsvendor_quantile(10.0, 10.0) == pytest.approx(0.5)


def test_settlement_identity():
    """Committing the truth exactly must earn precisely the day-ahead value of production."""
    run = RunConfig()
    truth = truth_series(400)
    price = np.full(400, 70.0)
    rebap = np.full(400, 120.0)
    pos, trades = b2_zero_imbalance(truth, run)
    r = settle(pos, trades, truth, price, rebap, price, run)
    assert r["imbalance_mwh"] == pytest.approx(0.0)
    assert r["net_revenue"] == pytest.approx(np.sum(truth * price) * run.dt)


def test_speculation_ceiling_dominates_zero_imbalance():
    """The genuine ceiling must beat the zero-imbalance reference, by construction."""
    run = RunConfig()
    truth = truth_series(600)
    price = 70 + 20 * RNG.standard_normal(600)
    rebap = rebap_series(600, price, np.diff(truth, prepend=truth[0]),
                         run.market, np.random.default_rng(2))
    ref = settle(*b2_zero_imbalance(truth, run), truth, price, rebap, price, run)
    ceil = settle(*b2_perfect_speculation(truth, price, rebap, run, CAP),
                  truth, price, rebap, price, run)
    assert ceil["net_revenue"] >= ref["net_revenue"] - 1e-6


def test_deadband_reduces_traded_volume(store):
    """B3's whole mechanism: trade less than B1 by ignoring small forecast revisions.

    Regression test - an earlier B3 compared two constants, so its condition was either
    always or never true and it was numerically identical to the newsvendor policy.
    """
    run = RunConfig()
    truth = truth_series()
    n = len(truth)
    price = np.full(n, 75.0)
    rebap = rebap_series(n, price, np.diff(truth, prepend=truth[0]),
                         run.market, np.random.default_rng(3))
    cs, cl = imbalance_cost_asymmetry(rebap, price)

    b1 = settle(*b1_point_intraday(store, n, run), truth, price, rebap, price, run)
    b3 = settle(*b3_stochastic_mpc(store, n, run, cs, cl), truth, price, rebap, price, run)
    assert b3["traded_mwh"] < b1["traded_mwh"]
    assert b3["id_cost"] < b1["id_cost"]


def test_intraday_correction_reduces_imbalance(store):
    run = RunConfig()
    truth = truth_series()
    n = len(truth)
    price = np.full(n, 75.0)
    rebap = np.full(n, 130.0)
    b0 = settle(*b0_day_ahead_only(store, n, run), truth, price, rebap, price, run)
    b1 = settle(*b1_point_intraday(store, n, run), truth, price, rebap, price, run)
    assert b1["imbalance_mwh"] < b0["imbalance_mwh"]


def test_rebap_is_heavy_tailed():
    """A Gaussian imbalance price would remove the feature the whole project is about."""
    price = np.full(20000, 70.0)
    r = rebap_series(20000, price, RNG.standard_normal(20000), MarketConfig(),
                     np.random.default_rng(4))
    z = (r - r.mean()) / r.std()
    assert float(np.mean(z ** 4)) > 3.5, "excess kurtosis expected relative to a normal"

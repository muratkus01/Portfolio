"""Accounting of the benchmark ladder: what each rung is credited with, and the invariant."""
from __future__ import annotations

import numpy as np
import pytest

import hybrid.baselines as baselines
from hybrid.baselines import run_ladder
from hybrid.config import PlantConfig, RunConfig
from hybrid.plant import simulate


def window(n: int, seed: int = 0):
    """Wind and PV in MW and a day-ahead price in EUR/MWh, starting at midnight."""
    rng = np.random.default_rng(seed)
    h = np.arange(n) * 0.25 % 24
    wind = np.clip(28 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 30 * (pv / 30) \
        + 15 * rng.standard_normal(n)
    return wind, pv, price


# ------------------------------------------------------------------ invariant tolerance
def test_perfect_forecasts_do_not_trip_the_ladder_invariant():
    """Regression: with forecast_sigma = 0, B3 beat B2 by solver rounding and the run raised.

    PuLP reads CBC's solution with about eight significant digits. B2 executes one plan, so
    its rounding residue stays in the result; B3 re-solves from the true state every step and
    does not. The gap is a few 1e-9 of the objective (0.0013 EUR on a 210,894 EUR week of 2024
    data), which passed the old absolute 1e-3 EUR tolerance once the objective was large. The
    plant is scaled 20x so that half a day earns what a published 20-day window earns.
    """
    k = 20.0
    plant = PlantConfig(wind_mw=50 * k, pv_mw=30 * k, bess_mw=20 * k, bess_mwh=40 * k,
                        conn_mw=80 * k)
    run = RunConfig(dt=0.25, horizon_steps=48, forecast_sigma=0.0, plant=plant)
    wind, pv, price = window(48, seed=1)

    res = run_ladder(wind * k, pv * k, price, run, premium_rate=8.0, include_b3=True)

    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    assert res["B3 rolling MPC"]["cost"]["net_revenue"] == pytest.approx(b2, rel=1e-6)


def test_a_genuine_ladder_violation_still_raises(monkeypatch):
    """The relative tolerance must not hide a real defect.

    A "perfect foresight" rung that throws away 0.1 % of the generation loses to the
    no-battery plant by about a thousand times the tolerance, so the run must stop.
    """
    run = RunConfig(dt=0.25)
    wind, pv, price = window(16)

    def wasteful_b2(wind, pv, eff_price, run, export_cap=None):
        n = len(wind)
        return simulate(wind, pv, np.zeros(n), np.full(n, 0.001), run.dt, run.plant,
                        export_cap=export_cap)

    monkeypatch.setattr(baselines, "b2_perfect", wasteful_b2)
    with pytest.raises(AssertionError, match="ladder invariant violated"):
        run_ladder(wind, pv, price, run, premium_rate=8.0, include_b3=False)

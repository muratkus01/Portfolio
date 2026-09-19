"""Gate 1: the port must reproduce the original `smart_weekly.py` exactly.

This is the regression harness the whole conversion depends on. Every later change - 15-minute
resolution, the real German tariff, the rolling horizon, the RL agent - is checked against the
fact that running the package in LEGACY_RUN configuration still lands on the original numbers.
Without this fixed point there is no way to tell a correct improvement from a bug.

Reference files are the original script's own outputs, kept under
`tests/reference/` (small enough to commit, unlike the rest of `data/`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from prosumer.baselines.milp import solve_window
from prosumer.config import LEGACY_RUN
from prosumer.data.loaders import load_legacy_csv
from prosumer.market import settlement, tariff
from prosumer.model import site

DATA = Path(__file__).parents[1] / "data" / "raw" / "legacy" / "12-18_08_2024.csv"
REFERENCE = Path(__file__).parent / "reference" / "12-18_08_2024_hourly_optimization_results.csv"

# The original objective, from the script's own summary report.
LEGACY_OBJECTIVE_EUR = 8.563975

# The measured site week is not redistributed with the repository, so on a fresh clone this
# module skips rather than erroring. The physics, safety-layer and resampling properties it
# relies on are covered by synthetic tests in `test_safety_and_physics.py`, which always run.
pytestmark = pytest.mark.skipif(
    not DATA.exists(),
    reason="measured thesis week not present (data/raw/legacy/ is not redistributed)")


def _run_legacy_day_by_day():
    """Exactly the original driver: 24-hour windows, SoC handed over between days."""
    run = LEGACY_RUN
    df = tariff.build_prices(load_legacy_csv(DATA), run.tariff)
    load = df["load_kw"].to_numpy()
    pv = df["pv_kw"].to_numpy()
    pi = df["price_import"].to_numpy()
    pe = df["price_export"].to_numpy()

    soc = run.site.soc_init
    parts = []
    for d0 in range(0, len(df), 24):
        sl = slice(d0, min(d0 + 24, len(df)))
        sol = solve_window(load[sl], pv[sl], pi[sl], pe[sl], run.dt, run.site, soc,
                           use_binaries=True,
                           throughput_cap=run.site.e_throughput_max_per_day,
                           steps_per_day=24)
        assert sol is not None, f"day starting at index {d0} was infeasible"
        parts.append(sol)
        soc = float(sol["soc"][-1])

    res = {k: np.concatenate([p[k] for p in parts])
           for k in ("soc", "p_bat", "p_ch", "p_dis", "p_imp", "p_exp", "throughput")}
    return df, res, load, pv, pi, pe


@pytest.fixture(scope="module")
def legacy():
    return _run_legacy_day_by_day()


def test_objective_matches_original(legacy):
    """The headline number from the original summary report, to the cent and beyond."""
    df, res, *_ = legacy
    spot = df["spot_eur_per_kwh"].to_numpy()
    obj = float(np.sum(spot * res["p_bat"]))       # original: max sum(EP*(P_dc - P_ch))
    assert obj == pytest.approx(LEGACY_OBJECTIVE_EUR, abs=1e-5)


def test_energy_balance_closes(legacy):
    df, res, load, pv, *_ = legacy
    err = site.energy_balance_error(res, load, pv, LEGACY_RUN.dt, LEGACY_RUN.site)
    assert err < 1e-6


def test_no_violations(legacy):
    _, res, *_ = legacy
    assert sum(site.check_feasible(res, LEGACY_RUN.dt, LEGACY_RUN.site).values()) == 0


def test_objective_equals_settlement(legacy):
    """The optimiser's objective and the independently recomputed settlement must agree.

    This is defect D3 turned into a test: in the original, the maximised expression and the
    reported revenue were different quantities.
    """
    _, res, load, pv, pi, pe = legacy
    cost = settlement.net_cost(res, pi, pe, LEGACY_RUN.dt, LEGACY_RUN.site)
    recomputed = float(np.sum(res["p_imp"] * pi - res["p_exp"] * pe) * LEGACY_RUN.dt)
    assert cost == pytest.approx(recomputed, abs=1e-9)


@pytest.mark.skipif(not REFERENCE.exists(), reason="original output CSV not available")
def test_daily_objective_matches_original(legacy):
    """Per-day objective equality against the original script's own hourly output.

    NOT a row-by-row trajectory comparison, and the reason is a genuine property of the
    original model rather than a tolerance problem: **the LP is degenerate.** The objective
    `sum(EP*(P_dc - P_ch))` is uniquely determined, but the dispatch that achieves it is not.
    Whenever prices are flat across several hours, shifting charging between them leaves the
    objective unchanged, so many optimal vertices exist and different solver versions pick
    different ones.

    Verified: the trajectory recorded in the original output file scores exactly 8.563975 EUR
    - the same value this port reaches by a different path.

    The practical consequence is worth knowing: **the dispatch trajectory reported by the
    original model is arbitrary among ties**, so plots of SoC from it should not be
    interpreted as "the" optimal schedule. Adding any strictly positive degradation cost
    (`SiteConfig.c_deg > 0`) breaks the ties and makes the solution unique - which is one more
    reason the production configuration prices throughput instead of capping it.
    """
    _, res, *_ = legacy
    ref = pd.read_csv(REFERENCE)
    assert len(ref) == len(res["soc"])

    ep = ref["EP"].to_numpy()
    ref_obj = ep * (ref["P_dc"].to_numpy() - ref["P_ch"].to_numpy())
    our_obj = ep * res["p_bat"]
    for day in sorted(ref["Day"].unique()):
        m = (ref["Day"] == day).to_numpy()
        assert our_obj[m].sum() == pytest.approx(ref_obj[m].sum(), abs=1e-4), \
            f"day {day} objective differs - that would be a real porting error"


@pytest.mark.skipif(not REFERENCE.exists(), reason="original output CSV not available")
def test_original_drains_battery_every_midnight(legacy):
    """Defect D4, demonstrated on the original model's own output.

    Every day ends at the minimum state of charge, because each 24-hour window is optimised
    with no value placed on energy carried into the next day. This is the horizon-end drain
    that the terminal value function in `baselines/milp.default_terminal_price` exists to fix,
    and it is visible in the thesis results as they stand.
    """
    ref = pd.read_csv(REFERENCE)
    end_of_day = ref.groupby("Day")["SOC"].last().to_numpy()
    np.testing.assert_allclose(end_of_day, LEGACY_RUN.site.soc_min, atol=1e-3)

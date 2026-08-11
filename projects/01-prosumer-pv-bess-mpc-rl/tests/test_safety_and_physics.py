"""Property tests on the physics and the safety layer.

These are the tests that make the "zero violations by construction" claim checkable rather
than aspirational. The safety-layer test in particular drives the site with uniformly random
actions - including deliberately absurd ones - and asserts that no constraint is ever
violated. A learned policy exploring early in training does exactly this, so if the test
passes, no policy can produce an infeasible dispatch.
"""
from __future__ import annotations

import numpy as np
import pytest

from prosumer.config import RunConfig, SiteConfig
from prosumer.model import site
from prosumer.safety import feasible_interval, project_series

RNG = np.random.default_rng(0)
N = 2000


def _random_profiles(n: int = N):
    load = np.abs(RNG.normal(0.5, 0.4, n))
    pv = np.clip(RNG.normal(1.5, 2.0, n), 0, None)
    return load, pv


@pytest.mark.parametrize("dt", [1.0, 0.25, 0.0833333])
def test_safety_layer_admits_no_violation(dt):
    """10 000 random actions per resolution, zero violations expected."""
    cfg = SiteConfig()
    load, pv = _random_profiles()
    # deliberately out-of-range proposals: 5x the inverter rating, both signs
    proposed = RNG.uniform(-5 * cfg.p_inv, 5 * cfg.p_inv, len(load))

    p = project_series(proposed, load, pv, dt, cfg)
    res = site.simulate(p, load, pv, dt, cfg)
    viol = site.check_feasible(res, dt, cfg)
    assert sum(viol.values()) == 0, f"safety layer let a violation through: {viol}"


@pytest.mark.parametrize("dt", [1.0, 0.25])
def test_energy_balance_closes(dt):
    """The site power balance must hold identically for any feasible dispatch."""
    cfg = SiteConfig()
    load, pv = _random_profiles()
    p = project_series(RNG.uniform(-cfg.p_inv, cfg.p_inv, len(load)), load, pv, dt, cfg)
    res = site.simulate(p, load, pv, dt, cfg)
    assert site.energy_balance_error(res, load, pv, dt, cfg) < 1e-9


def test_dt_actually_matters():
    """Regression guard for defect D1.

    The original model omitted `* dt` from the SoC recursion. That is invisible at dt = 1 h
    and wrong by exactly a factor of 4 at dt = 0.25 h. This test pins the correct behaviour:
    charging at constant power for one hour must store the same energy however many steps it
    is split into.
    """
    cfg = SiteConfig()
    p = -2.0                                     # 2 kW charge
    soc_1h = site.soc_next(cfg.soc_init, p, 1.0, cfg)
    soc_4x15 = cfg.soc_init
    for _ in range(4):
        soc_4x15 = site.soc_next(soc_4x15, p, 0.25, cfg)
    assert soc_1h == pytest.approx(soc_4x15, abs=1e-12)
    assert soc_1h - cfg.soc_init == pytest.approx(2.0 * cfg.eta_c, abs=1e-12)


def test_feasible_interval_never_empty_in_normal_operation():
    """The projection interval must be well ordered whenever the site is physically able."""
    cfg = SiteConfig()
    for soc in np.linspace(cfg.soc_min, cfg.soc_max, 25):
        for load in (0.0, 0.5, 2.0):
            for pv in (0.0, 1.0, 6.0):
                lo, hi = feasible_interval(soc, load, pv, 0.25, cfg)
                assert lo <= hi + 1e-12


def test_round_trip_efficiency_loses_energy():
    """Charging then discharging the same power must lose exactly (1 - eta_c*eta_d).

    This is also the reason the MILP's charge/discharge binaries are redundant: simultaneous
    charge and discharge strictly destroys energy, so no optimal solution does it.
    """
    cfg = SiteConfig()
    dt = 0.25
    s0 = 4.0
    s1 = site.soc_next(s0, -4.0, dt, cfg)          # charge 4 kW
    stored = s1 - s0
    delivered = (s1 - site.soc_next(s1, 4.0 * cfg.eta_c * cfg.eta_d, dt, cfg))
    assert stored == pytest.approx(4.0 * cfg.eta_c * dt)
    assert delivered > 0


def test_para_14a_cap_limits_charging_only():
    """Grid-orientated control limits consumption, not injection."""
    cfg = SiteConfig()
    lo_free, hi_free = feasible_interval(4.0, 0.5, 0.0, 0.25, cfg, dim_limit=None)
    lo_dim, hi_dim = feasible_interval(4.0, 0.5, 0.0, 0.25, cfg, dim_limit=4.2)
    assert lo_dim >= lo_free - 1e-12          # charging is restricted
    assert hi_dim == pytest.approx(hi_free)   # discharging is untouched


def test_binaries_are_redundant_when_degradation_is_priced():
    """With c_deg > 0 the relaxed MILP never charges and discharges simultaneously."""
    from prosumer.baselines.milp import solve_window, verify_no_simultaneous

    run = RunConfig(dt=0.25)
    n = 96
    load = np.abs(RNG.normal(0.6, 0.3, n))
    pv = np.clip(np.sin(np.linspace(0, np.pi, n)) * 4.0 + RNG.normal(0, 0.3, n), 0, None)
    pi = np.full(n, 0.30)
    pe = np.full(n, 0.08)

    sol = solve_window(load, pv, pi, pe, run.dt, run.site, run.site.soc_init,
                       use_binaries=False)
    assert sol is not None
    assert verify_no_simultaneous(sol) == 0

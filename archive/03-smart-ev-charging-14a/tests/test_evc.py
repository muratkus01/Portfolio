"""Safety layer, allocation and tariff tests for the charging hub."""
from __future__ import annotations

import numpy as np
import pytest

from evc.allocate import (aggregate_floor, dimming_series, energy_price,
                          minimum_required_power, network_energy_price, project)
from evc.baselines import b0_uncontrolled, b1_equal_share, b3_price_greedy, cost, fairness
from evc.config import ARCHETYPES, Para14aConfig, RunConfig, SiteConfig, TariffConfig
from evc.sessions import generate


def make_run(**kw) -> RunConfig:
    base = dict(days=3, seed=0, site=SiteConfig(n_connectors=8, site_limit_kw=44.0))
    base.update(kw)
    return RunConfig(**base)


# ------------------------------------------------------------------ safety layer
def test_site_limit_is_never_exceeded():
    """Random, deliberately excessive proposals must never breach the connection."""
    run = make_run()
    rng = np.random.default_rng(0)
    n = run.site.n_connectors
    for _ in range(500):
        active = rng.random(n) < 0.7
        remaining = np.where(active, rng.uniform(0, 40, n), 0.0)
        steps_left = np.where(active, rng.integers(1, 40, n), 0)
        proposed = rng.uniform(0, 3 * run.site.p_connector_kw, n)
        p = project(proposed, remaining, steps_left, active, run)
        assert p.sum() <= run.site.site_limit_kw + 1e-6
        assert np.all(p >= -1e-9)
        assert np.all(p <= run.site.p_connector_kw + 1e-6)
        assert np.all(p[~active] == 0.0)


def test_dimming_cap_is_respected():
    run = make_run()
    rng = np.random.default_rng(1)
    n = run.site.n_connectors
    active = np.ones(n, dtype=bool)
    remaining = np.full(n, 30.0)
    steps_left = np.full(n, 20)
    p = project(rng.uniform(0, 11, n), remaining, steps_left, active, run, dim_cap=4.2)
    assert p.sum() <= 4.2 + 1e-6


def test_min_current_rule():
    """A connector is either off or at least at the minimum charging current."""
    run = make_run()
    n = run.site.n_connectors
    active = np.ones(n, dtype=bool)
    remaining = np.full(n, 20.0)
    steps_left = np.full(n, 60)
    p = project(np.full(n, 1.0), remaining, steps_left, active, run)
    on = p > 1e-9
    assert np.all(p[on] >= run.site.p_min_kw - 1e-6)


def test_minimum_required_power_is_the_average_rate():
    need = minimum_required_power(np.array([10.0]), np.array([8]), 0.25, 11.0)
    assert need[0] == pytest.approx(10.0 / (8 * 0.25))


def test_aggregate_floor_detects_collective_infeasibility():
    """Individually comfortable, collectively impossible - the case the per-connector test misses.

    Three vehicles each need 40 kWh within 2 hours. Each needs 20 kW, under the 22 kW
    connector rating, so the per-connector check is satisfied. Together they need 60 kW from a
    44 kW site: the aggregate floor must be strictly positive and demand the site's full
    output immediately.
    """
    run = make_run(site=SiteConfig(n_connectors=8, site_limit_kw=44.0, p_connector_kw=22.0))
    active = np.array([True, True, True] + [False] * 5)
    remaining = np.array([40.0, 40.0, 40.0] + [0.0] * 5)
    steps_left = np.array([8, 8, 8] + [0] * 5)
    floor = aggregate_floor(remaining, steps_left, active, run, run.site.site_limit_kw)
    assert floor > run.site.site_limit_kw * 0.5


def test_aggregate_floor_zero_when_ample_time():
    run = make_run()
    active = np.array([True] + [False] * 7)
    remaining = np.array([10.0] + [0.0] * 7)
    steps_left = np.array([200] + [0] * 7)
    assert aggregate_floor(remaining, steps_left, active, run,
                           run.site.site_limit_kw) == pytest.approx(0.0)


def test_tight_deadline_is_protected_from_shedding():
    """When capacity is short, the vehicle closest to its deadline keeps its power."""
    run = make_run(site=SiteConfig(n_connectors=4, site_limit_kw=15.0))
    active = np.ones(4, dtype=bool)
    remaining = np.array([10.0, 10.0, 10.0, 10.0])
    steps_left = np.array([4, 40, 40, 40])          # connector 0 is nearly out of time
    p = project(np.full(4, 11.0), remaining, steps_left, active, run)
    assert p[0] == max(p), "the tightest deadline must not be the one that gets shed"
    assert p.sum() <= 15.0 + 1e-6


# ------------------------------------------------------------------ tariff
def test_module_2_reduces_the_network_charge():
    hours = np.arange(24)
    base = network_energy_price(hours, TariffConfig(para_14a_module="none"))
    m2 = network_energy_price(hours, TariffConfig(para_14a_module="module_2"))
    assert np.all(m2 < base)


def test_module_3_is_time_variable():
    hours = np.arange(24)
    cfg = TariffConfig(para_14a_module="module_3")
    p = network_energy_price(hours, cfg)
    assert p[np.isin(hours, cfg.module_3_high_hours)].max() > \
        p[np.isin(hours, cfg.module_3_low_hours)].max()


def test_module_1_leaves_the_marginal_price_unchanged():
    """Module 1 is a flat annual credit, so it must not alter the marginal price."""
    hours = np.arange(24)
    assert np.allclose(network_energy_price(hours, TariffConfig(para_14a_module="module_1")),
                       network_energy_price(hours, TariffConfig(para_14a_module="none")))


def test_energy_price_includes_all_components():
    cfg = TariffConfig(vat=0.0)
    p = energy_price(np.array([0.1]), np.array([12]), cfg)
    expected = 0.1 + cfg.supplier_margin + cfg.network_energy + cfg.levies + cfg.electricity_tax
    assert p[0] == pytest.approx(expected)


# ------------------------------------------------------------------ end to end
def test_ladder_runs_and_respects_the_limit():
    run = make_run(days=3, archetype=ARCHETYPES["workplace"],
                   site=SiteConfig(n_connectors=12, site_limit_kw=90.0))
    sessions = generate(run)
    n_steps = run.days * run.steps_per_day
    spot = np.full(n_steps, 0.08)
    hours = np.tile(np.repeat(np.arange(24), 4), run.days)[:n_steps]
    for fn in (b0_uncontrolled, b1_equal_share, b3_price_greedy):
        res = fn(sessions, n_steps, run, spot)
        assert res["site_kw"].max() <= run.site.site_limit_kw + 1e-6
        assert 0.0 <= fairness(res) <= 1.0 + 1e-9
        c = cost(res, spot, hours, run)
        assert c["net_cost"] == pytest.approx(
            c["energy_cost"] + c["peak_cost"] - c["thg_revenue"] - c["module_1_credit"])


def test_price_awareness_lowers_energy_cost():
    """B3 must beat a price-blind equal share on the energy component."""
    run = make_run(days=5, site=SiteConfig(n_connectors=12, site_limit_kw=90.0))
    sessions = generate(run)
    n_steps = run.days * run.steps_per_day
    rng = np.random.default_rng(3)
    spot = 0.08 + 0.05 * np.sin(np.arange(n_steps) * 2 * np.pi / 96) + 0.01 * rng.standard_normal(n_steps)
    hours = np.tile(np.repeat(np.arange(24), 4), run.days)[:n_steps]
    c1 = cost(b1_equal_share(sessions, n_steps, run, spot), spot, hours, run)
    c3 = cost(b3_price_greedy(sessions, n_steps, run, spot), spot, hours, run)
    assert c3["energy_cost"] < c1["energy_cost"]


def test_dimming_series_shape_and_cap():
    run = make_run()
    n = run.days * run.steps_per_day
    hours = np.tile(np.repeat(np.arange(24), 4), run.days)[:n]
    cfg = Para14aConfig(enabled=True, events_per_year=2000.0)
    caps = dimming_series(n, hours, run, cfg)
    assert caps is not None and len(caps) == n
    assert np.all((caps == np.inf) | (caps == cfg.p_min_total_kw))
    assert np.isfinite(caps).any(), "with this frequency some events must occur"
    assert dimming_series(n, hours, run, Para14aConfig(enabled=False)) is None


# ------------------------------------------------------------------ regressions (2026-09-19)
def test_sessions_past_the_window_are_censored_not_missed():
    """Regression: departures were truncated at the end of the simulated window.

    A depot car arriving on the final evening was squeezed into the last few hours, every such
    car demanded full power at once, and the site "missed" departures no controller could meet.
    The miss count was identical across all controllers, which is what gave it away. Such
    sessions are now right-censored: simulated, never scored.
    """
    run = RunConfig(days=2, seed=0, archetype=ARCHETYPES["depot"],
                    site=SiteConfig(n_connectors=16, site_limit_kw=100.0))
    sessions = generate(run)
    n_steps = run.days * run.steps_per_day
    censored = [s for s in sessions if s.censored]
    assert censored, "evening depot arrivals on the last day must run past the window"
    for s in censored:
        assert s.depart_true == n_steps
        assert s.depart_declared > s.arrive
    for s in sessions:
        if not s.censored:
            assert s.depart_true <= n_steps and s.depart_declared <= s.depart_true

    spot = np.full(n_steps, 0.08)
    res = b0_uncontrolled(sessions, n_steps, run, spot)
    assert res["censored_sessions"] == len(censored)
    assert res["missed_departures"] == 0


def test_min_current_does_not_undo_the_reserve():
    """Regression: a car past its DECLARED departure but still plugged in has per-connector
    need zero, so the minimum-current rule switched off the small top-up the aggregate floor
    had just assigned it. Power the floor requires must survive rounding and shedding."""
    run = make_run(site=SiteConfig(n_connectors=1, site_limit_kw=50.0))
    p = project(np.array([0.0]), remaining_kwh=np.array([0.5]), steps_left=np.array([0]),
                active=np.array([True]), run=run)
    assert p[0] == pytest.approx(0.5 / run.dt), "the last 0.5 kWh must be delivered, not dropped"

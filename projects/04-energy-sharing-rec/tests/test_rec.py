"""Allocation mechanism invariants, settlement identities and stability checks."""
from __future__ import annotations

import numpy as np
import pytest

from rec.community import (allocate, feeder_violations, individual_rationality,
                           member_prices, member_profiles, pv_profile, settle)
from rec.config import REGIMES, RegimeConfig, RunConfig, default_members

MECHANISMS = ("static_key", "dynamic_proportional", "optimisation", "market")


def make_run(**kw) -> RunConfig:
    base = dict(days=5, seed=0, members=default_members(12, 0))
    base.update(kw)
    return RunConfig(**base)


@pytest.fixture(scope="module")
def setup():
    run = make_run()
    cons = member_profiles(run)
    gen = pv_profile(run, run.community.shared_pv_kwp)
    return run, cons, gen


# ------------------------------------------------------------------ allocation invariants
@pytest.mark.parametrize("mech", MECHANISMS)
def test_allocation_is_physically_valid(setup, mech):
    """Three invariants every mechanism must satisfy, whatever its fairness properties."""
    run, cons, gen = setup
    alloc = allocate(gen, cons, mech, run)
    assert np.all(alloc >= -1e-9), "negative allocation"
    assert np.all(alloc <= cons + 1e-6), "a member cannot be allocated more than it consumes"
    assert np.all(alloc.sum(axis=1) <= gen + 1e-6), "more allocated than generated"


@pytest.mark.parametrize("mech", MECHANISMS)
def test_no_allocation_without_generation(setup, mech):
    run, cons, gen = setup
    alloc = allocate(np.zeros_like(gen), cons, mech, run)
    assert alloc.sum() == pytest.approx(0.0)


def test_optimisation_allocates_at_least_as_much_as_static(setup):
    """Greedy-to-highest-use cannot leave generation unused that a fixed key would have used."""
    run, cons, gen = setup
    static = allocate(gen, cons, "static_key", run).sum()
    opt = allocate(gen, cons, "optimisation", run).sum()
    assert opt >= static - 1e-6


def test_unknown_mechanism_raises(setup):
    run, cons, gen = setup
    with pytest.raises(ValueError):
        allocate(gen, cons, "blockchain", run)


# ------------------------------------------------------------------ settlement
def test_settlement_energy_balance(setup):
    """Shared + grid must equal total consumption, exactly."""
    run, cons, gen = setup
    alloc = allocate(gen, cons, "dynamic_proportional", run)
    s = settle(cons, alloc, gen, run)
    assert s["shared_kwh"] + s["grid_kwh"] == pytest.approx(
        float(cons.sum() * run.dt), rel=1e-9)


def test_member_bills_sum_to_community_bill(setup):
    run, cons, gen = setup
    alloc = allocate(gen, cons, "market", run)
    s = settle(cons, alloc, gen, run)
    assert s["member_bills"].sum() == pytest.approx(s["community_bill"])


def test_sharing_beats_individual_when_the_internal_price_is_low(setup):
    """With shared energy priced below the grid, the community must be better off in total."""
    run, cons, gen = setup
    cheap = RegimeConfig(**{**REGIMES["para_42b"].__dict__, "internal_price": 0.05})
    run2 = run.with_(regime=cheap)
    alloc = allocate(gen, cons, "dynamic_proportional", run2)
    s = settle(cons, alloc, gen, run2)
    assert s["net_community_cost"] < s["community_baseline"]


def test_high_internal_price_can_make_exporting_better():
    """A real design trap: if the internal price is too low relative to the feed-in tariff,
    the community is better off EXPORTING than sharing.

    This is not a modelling artefact - it is the reason the internal price is a decision
    variable for a community operator rather than a constant, and it is why the mechanism
    comparison must always be read together with the price it assumes.
    """
    run = make_run()
    cons = member_profiles(run)
    gen = pv_profile(run, run.community.shared_pv_kwp)
    low = RegimeConfig(**{**REGIMES["para_42b"].__dict__,
                          "internal_price": 0.01, "feed_in_tariff": 0.40})
    run2 = run.with_(regime=low)
    a_share = allocate(gen, cons, "optimisation", run2)
    s_share = settle(cons, a_share, gen, run2)
    s_none = settle(cons, np.zeros_like(cons), gen, run2)
    assert s_none["net_community_cost"] < s_share["net_community_cost"]


def test_prices_are_ordered_sensibly():
    shared, grid, export = member_prices(REGIMES["para_42b"])
    assert export < shared < grid, "sharing should sit between exporting and buying"


# ------------------------------------------------------------------ stability
def test_individual_rationality_reported_per_member(setup):
    run, cons, gen = setup
    alloc = allocate(gen, cons, "static_key", run)
    ir = individual_rationality(settle(cons, alloc, gen, run))
    assert set(ir) >= {"members_worse_off", "ir_satisfied", "min_saving", "gini"}
    assert 0.0 <= ir["gini"] <= 1.0


def test_mechanisms_differ_in_distribution_not_only_in_total(setup):
    """The project's premise, as a test.

    With obedient members and no flexibility the community TOTAL is largely
    mechanism-independent - allocation only moves money between members. What differs is the
    distribution. Once members respond to their own price signal that stops being true, and
    measuring the difference is what the multi-agent work is for.
    """
    run, cons, gen = setup
    ginis = []
    for mech in MECHANISMS:
        s = settle(cons, allocate(gen, cons, mech, run), gen, run)
        ginis.append(individual_rationality(s)["gini"])
    assert max(ginis) - min(ginis) > 1e-6, "mechanisms must differ in who benefits"


# ------------------------------------------------------------------ physical feasibility
def test_feeder_violations_detected_when_limit_is_tiny(setup):
    run, cons, gen = setup
    tight = run.community.__class__(**{**run.community.__dict__, "feeder_limit_kw": 1.0})
    alloc = allocate(gen, cons, "market", run)
    assert feeder_violations(cons, alloc, gen, tight) > 0
    assert feeder_violations(cons, alloc, gen, run.community) >= 0


def test_correlation_moves_the_distribution_not_the_total():
    """Inter-member correlation changes WHO benefits, barely the community total. Why:

    With one shared generation pool and no binding per-member constraint, the energy shared in
    a step is `min(generation, total consumption)` - a function of the AGGREGATE alone. Moving
    correlation redistributes consumption between members without materially changing their
    sum, so the community total is nearly invariant while the benefit distribution is not.

    **This is a limitation of the profile generator, not a general truth about communities.**
    Here inter-member variation is multiplicative noise around a shared diurnal shape, so the
    aggregate shape hardly moves. Real heterogeneity is in the *shape* - shift workers, empty
    daytime flats, a bakery starting at 04:00 - and that does change the aggregate's overlap
    with PV. Fitting per-member shapes from the measured HTW Berlin profiles is the documented
    upgrade, and until it is done, any claim about the *size* of the community benefit from
    this generator should be treated as indicative only.
    """
    profiles, totals, ginis = [], [], []
    for corr in (0.0, 0.9):
        run = make_run(correlation=corr)
        cons = member_profiles(run)
        gen = pv_profile(run, run.community.shared_pv_kwp)
        s = settle(cons, allocate(gen, cons, "optimisation", run), gen, run)
        profiles.append(cons)
        totals.append(s["community_baseline"] - s["net_community_cost"])
        ginis.append(individual_rationality(s)["gini"])

    # the knob does change the profiles ...
    assert not np.allclose(profiles[0], profiles[1]), "correlation had no effect on profiles"

    # ... but currently changes neither the total nor the distribution materially.
    # Recorded as an assertion so that the day the generator gains per-member SHAPE
    # heterogeneity, this test fails loudly and the limitation note is revisited rather than
    # quietly left standing in the README.
    assert totals[0] == pytest.approx(totals[1], rel=0.02)
    assert ginis[0] == pytest.approx(ginis[1], abs=0.01)

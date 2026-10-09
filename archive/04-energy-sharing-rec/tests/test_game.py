"""Cooperative game properties: the axioms and theorems the analysis relies on, as tests."""
from __future__ import annotations

import numpy as np
import pytest

from rec.community import (allocate, individual_rationality, member_profiles, pv_profile,
                           settle, sharing_spread)
from rec.config import REGIMES, RegimeConfig, RunConfig, default_members
from rec.game import (Game, all_coalition_values, break_even_network_charge, build_game,
                      coalition_value, core_excess, owen_allocation, shapley_exact,
                      shapley_monte_carlo)


@pytest.fixture(scope="module")
def small():
    run = RunConfig(days=4, members=default_members(7, 0), regime=REGIMES["para_42b"])
    cons = member_profiles(run)
    gen = pv_profile(run, 45.0)
    game = build_game(run, cons, gen)
    return run, cons, gen, game, all_coalition_values(game)


# ------------------------------------------------------------------ characteristic function
def test_empty_coalition_is_worthless(small):
    *_, game, v = small
    assert v[0] == 0.0 and coalition_value(game, []) == 0.0


def test_table_matches_direct_evaluation(small):
    *_, game, v = small
    rng = np.random.default_rng(0)
    for _ in range(25):
        mask = int(rng.integers(1, 1 << game.n))
        members = [i for i in range(game.n) if mask >> i & 1]
        assert v[mask] == pytest.approx(coalition_value(game, members), rel=1e-12)


def test_game_is_superadditive(small):
    """Pooling can never hurt: v(S u T) >= v(S) + v(T) for disjoint S, T."""
    *_, game, v = small
    n = game.n
    rng = np.random.default_rng(1)
    for _ in range(300):
        a = int(rng.integers(1, 1 << n))
        b = int(rng.integers(1, 1 << n)) & ~a
        if b:
            assert v[a | b] >= v[a] + v[b] - 1e-9


def test_no_value_when_sharing_does_not_pay():
    """If the spread is non-positive, a rational coalition does not share: v = 0 everywhere."""
    reg = RegimeConfig(**{**REGIMES["para_42b"].__dict__, "feed_in_tariff": 0.50})
    run = RunConfig(days=2, members=default_members(5, 0), regime=reg)
    assert sharing_spread(reg) < 0
    game = build_game(run, member_profiles(run), pv_profile(run, 30.0))
    assert np.all(all_coalition_values(game) == 0.0)
    assert np.all(owen_allocation(game) == 0.0)


# ------------------------------------------------------------------ Shapley axioms
def test_shapley_is_efficient(small):
    *_, game, v = small
    assert shapley_exact(v, game.n).sum() == pytest.approx(v[-1], rel=1e-10)


def test_shapley_symmetry():
    """Identical members receive identical Shapley values."""
    T = 96
    c = np.tile(np.linspace(0.5, 2.0, T)[:, None], (1, 3))
    game = Game(g=np.full(T, 3.0), k=np.full(3, 1 / 3), c=c, dt=0.25, s=0.2)
    phi = shapley_exact(all_coalition_values(game), 3)
    assert np.allclose(phi, phi[0])


def test_shapley_null_player():
    """A member who neither consumes nor owns anything contributes, and receives, nothing."""
    T = 96
    c = np.column_stack([np.full(T, 1.0), np.full(T, 2.0), np.zeros(T)])
    game = Game(g=np.full(T, 2.5), k=np.array([0.5, 0.5, 0.0]), c=c, dt=0.25, s=0.2)
    phi = shapley_exact(all_coalition_values(game), 3)
    assert phi[2] == pytest.approx(0.0, abs=1e-12)


def test_monte_carlo_agrees_with_exact(small):
    *_, game, v = small
    exact = shapley_exact(v, game.n)
    est, se = shapley_monte_carlo(game, n_perm=3000, rng=np.random.default_rng(7))
    assert est.sum() == pytest.approx(v[-1], rel=1e-9), "every permutation is efficient"
    assert np.all(np.abs(est - exact) <= 5 * se + 1e-9)


# ------------------------------------------------------------------ core
def test_owen_allocation_is_in_the_core(small):
    """Owen's theorem for linear production games, checked over every coalition."""
    *_, game, v = small
    x = owen_allocation(game)
    assert x.sum() == pytest.approx(v[-1], rel=1e-9)
    res = core_excess(game, x, v)
    assert res["exhaustive"] and res["in_core"], res


def test_core_check_detects_a_bad_allocation(small):
    """Giving everything to one member must be blocked by the others."""
    *_, game, v = small
    x = np.zeros(game.n)
    x[0] = v[-1]
    res = core_excess(game, x, v)
    assert not res["in_core"] and res["blocking_coalitions"] > 0


def test_sampled_core_check_agrees_on_violations(small):
    *_, game, v = small
    x = np.zeros(game.n)
    x[0] = v[-1]
    res = core_excess(game, x, values=None, n_sample=400)
    assert not res["exhaustive"] and not res["in_core"]


# ------------------------------------------------------------------ link to the mechanisms
def test_static_key_creates_no_pooling_surplus(small):
    """A fixed ownership key gives each member only what their own share could cover, so its
    total equals the sum of standalone values: the key forgoes the whole cooperation gain."""
    run, cons, gen, game, v = small
    s = settle(cons, allocate(gen, cons, "static_key", run), gen, run)
    standalone = sum(v[1 << i] for i in range(game.n))
    assert s["coalition_value"] == pytest.approx(standalone, rel=1e-9)


def test_optimisation_reaches_the_grand_coalition_value(small):
    run, cons, gen, game, v = small
    s = settle(cons, allocate(gen, cons, "optimisation", run), gen, run)
    assert s["coalition_value"] == pytest.approx(v[-1], rel=1e-9)


def test_settlement_identity_holds_for_every_mechanism(small):
    """coalition value = consumer saving + owner gain = spread * shared kWh."""
    run, cons, gen, game, _ = small
    for mech in ("static_key", "dynamic_proportional", "optimisation", "market"):
        s = settle(cons, allocate(gen, cons, mech, run), gen, run)
        assert s["coalition_value"] == pytest.approx(s["consumer_saving"] + s["owner_gain"])
        assert s["coalition_value"] == pytest.approx(game.s * s["shared_kwh"], rel=1e-9)
        assert s["member_payoff"].sum() == pytest.approx(s["coalition_value"], rel=1e-9)
        assert s["community_baseline"] - s["net_community_cost"] == \
            pytest.approx(s["coalition_value"], rel=1e-9)


def test_mieterstrom_surcharge_rewards_sharing_not_exporting():
    """Regression test: the surcharge was once added to the EXPORT price."""
    base = REGIMES["mieterstrom"]
    no_surcharge = RegimeConfig(**{**base.__dict__, "mieterstrom_surcharge": 0.0})
    assert sharing_spread(base) - sharing_spread(no_surcharge) == \
        pytest.approx(base.mieterstrom_surcharge)
    run = RunConfig(days=2, members=default_members(6, 0), regime=base)
    cons, gen = member_profiles(run), pv_profile(run, 30.0)
    s = settle(cons, np.zeros_like(cons), gen, run)
    assert s["owner_gain"] == pytest.approx(0.0, abs=1e-9), "no sharing, no surcharge"


# ------------------------------------------------------------------ the policy question
def test_break_even_charge_zeroes_the_spread():
    run = RunConfig(members=default_members(4, 0), regime=REGIMES["energy_sharing"])
    x = break_even_network_charge(run)
    at = RegimeConfig(**{**REGIMES["energy_sharing"].__dict__, "shared_network_charge": x})
    assert sharing_spread(at) == pytest.approx(0.0, abs=1e-12)


def test_consumer_cliff_is_a_pricing_failure_not_a_value_failure():
    """At a charge where every consumer loses, the community still creates value, and a core
    settlement leaves no member worse off than going alone."""
    reg = RegimeConfig(**{**REGIMES["energy_sharing"].__dict__, "shared_network_charge": 0.05})
    run = RunConfig(days=3, members=default_members(8, 0), regime=reg)
    cons, gen = member_profiles(run), pv_profile(run, 48.0)
    s = settle(cons, allocate(gen, cons, "optimisation", run), gen, run)
    assert individual_rationality(s)["members_worse_off"] == run.members.__len__()
    assert s["coalition_value"] > 0

    game = build_game(run, cons, gen)
    v = all_coalition_values(game)
    x = owen_allocation(game)
    standalone = np.array([v[1 << i] for i in range(game.n)])
    assert np.all(x >= standalone - 1e-9)

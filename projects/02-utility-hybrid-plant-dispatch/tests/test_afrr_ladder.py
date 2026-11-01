"""aFRR capacity in the ladder's window LP: the reserve terms and their plans.

The battery is the default one: 20 MW, 40 MWh, a state of charge from 2 to 38 MWh and
efficiencies of 0.95. Every test here fails on db7043a, whose reserve module has no
ReserveSpec and whose package has no afrr_rungs, so this module does not import.
"""
from __future__ import annotations

import numpy as np
import pulp
import pytest

from hybrid import baselines
from hybrid.afrr_rungs import solve_window_reserve
from hybrid.config import MarketConfig, RunConfig
from hybrid.market import effective_price, settle
from hybrid.plant import simulate
from hybrid.reserve import PLAN_MARGIN, Payment, ReserveRule, ReserveSpec, add_reserve_terms

DT = 0.25
RUN = RunConfig(dt=DT)
PLANT = RUN.plant
PENALTY = MarketConfig().afrr_shortfall_penalty_eur_mwh
# the default rule: 4 hours of positive reserve, 1 hour of negative, at least 15 minutes of
# the battery's 20 MW
RULE = ReserveRule(4.0, 1.0, 5.0)
NO_FLOOR = ReserveRule(4.0, 1.0, 0.0)


def _window(n: int, seed: int = 0):
    """Wind and PV in MW and a day-ahead price in EUR/MWh, starting at midnight."""
    rng = np.random.default_rng(seed)
    h = np.arange(n) * DT % 24
    wind = np.clip(28 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 30 * (pv / 30) \
        + 15 * rng.standard_normal(n)
    return wind, pv, price


def _spec(block, fixed_up=0.0, fixed_down=0.0, paid_up=0.0, paid_down=0.0, rule=RULE,
          integer=False, margin=0.0) -> ReserveSpec:
    """A ReserveSpec on the products of `block`, each value per product or one for all."""
    count = int(np.max(block)) + 1

    def per_product(value):
        return np.broadcast_to(np.asarray(value, dtype=float), (count,))

    return ReserveSpec(per_product(fixed_up), per_product(fixed_down), per_product(paid_up),
                       per_product(paid_down), np.bincount(block) * DT, np.asarray(block),
                       rule, PENALTY, integer, margin)


def _solve(spec, wind, pv, price, soc0=PLANT.soc_min, run=RUN):
    """solve_window_reserve on a day-ahead price without a premium."""
    eff = effective_price(price, run.market, 0.0, run.dt)
    return solve_window_reserve(wind, pv, eff, run.dt, run, soc0, spec)


def _replay(plan, spec, wind, pv, price, soc0=PLANT.soc_min, run=RUN):
    """The plan through the safety layer with its reservations, and its settlement on the
    prices of `spec`."""
    res = simulate(wind, pv, plan.p_bat, plan.curt, run.dt, run.plant, soc0=soc0,
                   reserve_up=plan.r_up, reserve_down=plan.r_down, rule=spec.rule)
    payment = Payment(spec.paid_up[spec.block], spec.paid_down[spec.block], spec.block)
    return res, settle(res, price, run.dt, run.plant, run.market, premium_rate=0.0,
                       afrr=payment)


# ------------------------------------------------------------------ the reserve terms (C5)
def test_the_lp_prices_reserve_not_held_like_the_settlement():
    """A plan's objective is what the settlement pays its replay, reserve not held included.

    10 MW of positive reserve fixed on the first 4-hour product, from the empty start, cannot
    be held at all: the plan loses its payment on all 10 MW and pays the penalty on 40 MWh.
    Fixed again on the third product, once the battery is full, it falls short by
    10 x (1 - 36 x 0.95 / 40) = 1.45 MW, 5.8 MWh. And 1 MW at 6.5 MWh in store holds its
    4-hour band but not the floor, 5 MWh: 1 x (1 - 4.5 x 0.95 / 5) = 0.145 MW, 0.58 MWh. In
    both windows the LP's objective equals the net the settlement pays, at rel 1e-6.
    """
    n = 64
    block = np.arange(n) // 16
    wind, pv = np.full(n, 30.0), np.zeros(n)
    price = np.r_[np.full(48, 30.0), np.full(16, 150.0)]
    spec = _spec(block, fixed_up=[10.0, 0.0, 10.0, 0.0], paid_up=[15.0, 0.0, 15.0, 0.0])
    plan = _solve(spec, wind, pv, price)
    _, cost = _replay(plan, spec, wind, pv, price)

    assert cost["afrr_shortfall_up_mwh"] == pytest.approx(40.0 + 5.8, rel=1e-6)
    assert cost["afrr_capacity_cut"] == pytest.approx(15.0 * (40.0 + 5.8), rel=1e-6)
    assert plan.objective == pytest.approx(cost["net_revenue"], rel=1e-6)

    n = 16
    block, night = np.zeros(n, dtype=int), np.zeros(n)
    spec = _spec(block, fixed_up=1.0, paid_up=12.0)
    plan = _solve(spec, night, night, night, soc0=6.5)
    _, cost = _replay(plan, spec, night, night, night, soc0=6.5)

    assert cost["afrr_shortfall_up_mwh"] == pytest.approx(0.145 * 4, rel=1e-6)
    assert plan.objective == pytest.approx(cost["net_revenue"], rel=1e-6)


def test_reserve_terms_with_every_reservation_at_zero_add_nothing():
    """A spec whose reservations are all fixed at 0, though paid, leaves the energy LP as it
    was: the same variables, constraints and objective, as PuLP writes them out. Negative
    control: 1 MW fixed on one product adds its terms."""
    wind, pv, price = _window(32, seed=1)
    block = np.arange(32) // 16

    def model(spec):
        m, v = baselines._energy_lp(wind, pv, price - 60.0, DT, RUN, 12.0)
        if spec is not None:
            add_reserve_terms(m, v, spec, PLANT, 12.0, np.full(32, PLANT.conn_mw))
        return m.toDict()

    assert model(_spec(block, paid_up=20.0, paid_down=20.0)) == model(None)
    assert model(_spec(block, fixed_up=[1.0, 0.0], paid_up=20.0)) != model(None)


def test_a_decided_reservation_keeps_its_headroom_and_bands_with_the_margin():
    """Where the plan decides its reservations, it holds them as the safety layer does: at
    every step, power headroom both ways, connection headroom for positive reserve and
    export of at least the negative reservation; at both ends of every step, 4 hours of the
    positive reservation in store and 1 hour of the negative one as room. On this day each
    of these limits binds somewhere. Export and energy are planned on r x (1 + margin): 10 MW
    of generation that the plan will not store, at an energy price of 500 EUR/MWh, carry
    10 / (1 + 1e-4) MW of negative reserve, and 10 MW without the margin."""
    n = 96
    wind, pv, price = _window(n, seed=4)
    block = np.arange(n) // 16
    k = 1.0 + PLAN_MARGIN
    plan = _solve(_spec(block, np.nan, np.nan, 60.0, 15.0, rule=NO_FLOOR, margin=PLAN_MARGIN),
                  wind, pv, price)
    export = (wind + pv) * (1.0 - plan.curt) + plan.p_bat
    start, end = np.r_[PLANT.soc_min, plan.soc[:-1]], plan.soc
    tol = 1e-6

    assert plan.r_up.max() > 1.0 and plan.r_down.max() > 1.0
    assert np.all(plan.p_bat + plan.r_up <= PLANT.bess_mw + tol)
    assert np.all(plan.r_down - plan.p_bat <= PLANT.bess_mw + tol)
    assert np.all(export + plan.r_up <= PLANT.conn_mw + tol)
    assert np.all(export >= k * plan.r_down - tol)
    for soc in (start, end):
        assert np.all(soc >= PLANT.soc_min + k * plan.r_up * 4.0 / PLANT.eta_d - tol)
        assert np.all(soc <= PLANT.soc_max - k * plan.r_down * 1.0 * PLANT.eta_c + tol)

    one = np.zeros(16, dtype=int)
    for margin, expected in ((PLAN_MARGIN, 10.0 / k), (0.0, 10.0)):
        plan = _solve(_spec(one, fixed_down=np.nan, paid_down=20.0, rule=NO_FLOOR,
                            margin=margin), np.full(16, 10.0), np.zeros(16), np.full(16, 500.0))
        assert plan.r_down == pytest.approx(np.full(16, expected), rel=1e-9), margin


def test_whole_megawatt_decisions_keep_the_fifteen_minute_floor():
    """In whole MW a reservation keeps at least the floor, 15 minutes of the battery's 20 MW
    (5 MWh), as well as its band. At 6.5 MWh in store, 1 MW of positive reserve holds its
    4-hour band (4 MWh deliverable from 4.5 x 0.95 = 4.275) but not the floor, so the plan
    reserves 0 MW, and 1 MW without the floor, worth 12 x 1 x 4 = 48 EUR: a continuous
    decision would reserve 1.07 MW. At 36 MWh, 2 MW of negative reserve fit the 1-hour band
    (2 x 0.95 = 1.9 MWh of room below 38) but not the floor, so 0 MW, and 2 MW without it."""
    n = 16
    block, night = np.zeros(n, dtype=int), np.zeros(n)
    for rule, up, down in ((RULE, 0.0, 0.0), (NO_FLOOR, 1.0, 2.0)):
        spec = _spec(block, fixed_up=np.nan, paid_up=12.0, rule=rule, integer=True)
        plan = _solve(spec, night, night, night, soc0=6.5)
        assert plan.r_up.tolist() == [up] * n, rule
        assert plan.objective == pytest.approx(12.0 * up * 4.0, abs=1e-6), rule

        spec = _spec(block, fixed_down=np.nan, paid_down=30.0, rule=rule, integer=True)
        plan = _solve(spec, np.full(n, 10.0), night, np.full(n, 50.0), soc0=36.0)
        assert plan.r_down.tolist() == [down] * n, rule


def test_a_reserve_spec_refuses_what_the_lp_cannot_read():
    """A ReserveSpec holds one value per product, products numbered from 0 in time order,
    reservations of at least 0 MW (or NaN where decided), finite prices of at least 0 with
    a decided product paid above 0, hours and a penalty above 0, a margin of at least 0, and
    a floor only where decisions are whole MW. Anything else is refused, as is a spec whose
    steps differ from the model's."""
    block = np.array([0, 0, 1, 1])
    for kwargs in ({"fixed_up": [-1.0, 0.0]}, {"fixed_down": [np.inf, 0.0]},
                   {"paid_up": [np.nan, 1.0]}, {"paid_down": [-1.0, 1.0]},
                   {"fixed_up": [np.nan, 0.0]}, {"margin": -1e-4},
                   {"fixed_up": np.nan, "paid_up": 5.0}):
        with pytest.raises(ValueError, match="ReserveSpec"):
            _spec(block, **kwargs)
    zeros, ones = np.zeros(2), np.ones(2)
    for fields in ((np.zeros(3), zeros, ones, ones, ones, block, RULE, PENALTY, False, 0.0),
                   (zeros, zeros, ones, ones, np.zeros(2), block, RULE, PENALTY, False, 0.0),
                   (zeros, zeros, ones, ones, ones, block, RULE, 0.0, False, 0.0)):
        with pytest.raises(ValueError, match="ReserveSpec"):
            ReserveSpec(*fields)
    for wrong in (np.array([1, 1, 0, 0]), np.array([0, 0, 2, 2]), np.array([0.0, 0, 1, 1])):
        with pytest.raises(ValueError, match="ReserveSpec: block"):
            ReserveSpec(zeros, zeros, ones, ones, ones, wrong, RULE, PENALTY, False, 0.0)
    m, v = baselines._energy_lp(np.zeros(6), np.zeros(6), np.zeros(6), DT, RUN, 2.0)
    with pytest.raises(ValueError, match="spec has 4 steps and the model 6"):
        add_reserve_terms(m, v, _spec(block), PLANT, 2.0, np.full(6, 60.0))


def test_a_reserve_solve_stopped_short_of_optimal_is_not_used(monkeypatch):
    """A time limit stops CBC early, and PuLP then still reports the status Optimal, with
    the solution status "integer feasible". A fake solve gives both; the plan is used only
    where the solution is proven optimal."""
    def fake_solve(sol_status):
        def solve(problem, solver=None, **options):
            problem.assignVarsVals({variable.name: 0.0 for variable in problem.variables()})
            problem.assignStatus(pulp.LpStatusOptimal, sol_status)
            return problem.status
        return solve

    block = np.zeros(8, dtype=int)
    spec = _spec(block, fixed_up=np.nan, paid_up=10.0, integer=True)
    for sol_status, used in ((pulp.LpSolutionIntegerFeasible, False),
                             (pulp.LpSolutionOptimal, True)):
        monkeypatch.setattr(pulp.LpProblem, "solve", fake_solve(sol_status))
        plan = _solve(spec, np.zeros(8), np.zeros(8), np.zeros(8))
        assert (plan is not None) == used, sol_status


def test_a_plan_reads_its_reservations_within_the_rating_and_in_whole_mw(monkeypatch):
    """A solver's values may sit a residue outside a variable's bounds or off a whole MW,
    and the safety layer refuses a negative reservation. A fake solve returns 20.0000001,
    2.9999996 and -1e-12 MW: decided in whole MW they are read as 20, 3 and 0 MW; decided
    continuously, as 20, 2.9999996 and 0 MW. A fixed reservation is read as it was given."""
    values = {"r_up_0": 20.0000001, "r_up_1": 2.9999996, "r_down_0": -1e-12, "z_up_0": 1.0,
              "z_up_1": 1.0}

    def fake_solve(problem, solver=None, **options):
        problem.assignVarsVals({variable.name: values.get(variable.name, 0.0)
                                for variable in problem.variables()})
        problem.assignStatus(pulp.LpStatusOptimal, pulp.LpSolutionOptimal)
        return problem.status

    monkeypatch.setattr(pulp.LpProblem, "solve", fake_solve)
    block = np.repeat([0, 1], 4)
    for rule, integer, up in ((RULE, True, [20.0, 3.0]), (NO_FLOOR, False, [20.0, 2.9999996])):
        spec = _spec(block, fixed_up=np.nan, fixed_down=[np.nan, 4.5], paid_up=10.0,
                     paid_down=10.0, rule=rule, integer=integer)
        plan = _solve(spec, np.zeros(8), np.zeros(8), np.zeros(8))
        assert plan.r_up.tolist() == np.repeat(up, 4).tolist(), integer
        assert plan.r_down.tolist() == np.repeat([0.0, 4.5], 4).tolist(), integer

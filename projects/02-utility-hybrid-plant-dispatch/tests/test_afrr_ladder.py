"""aFRR capacity in the ladder: the reserve terms of the window LP, and B2 with aFRR.

The battery is the default one: 20 MW, 40 MWh, a state of charge from 2 to 38 MWh and
efficiencies of 0.95. Every test here fails on db7043a, whose reserve module has no
ReserveSpec and whose package has no afrr_rungs, so this module does not import; and on
1e1fd1d, whose reserve module has no rounding_bound and whose run_ladder takes no afrr.
"""
from __future__ import annotations

import numpy as np
import pulp
import pytest

from hybrid import afrr_rungs, baselines
from hybrid.afrr_rungs import solve_window_reserve
from hybrid.baselines import ladder_tolerance, run_ladder
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.market import effective_price, settle
from hybrid.plant import simulate
from hybrid.reserve import (PLAN_MARGIN, CapacityPrices, Payment, ReservePlan, ReserveRule,
                            ReserveSpec, add_reserve_terms, paid_prices, rounding_bound,
                            whole_megawatts)

DT = 0.25
RUN = RunConfig(dt=DT)
PLANT = RUN.plant
PENALTY = MarketConfig().afrr_shortfall_penalty_eur_mwh
# the default rule: 4 hours of positive reserve, 1 hour of negative, at least 15 minutes of
# the battery's 20 MW
RULE = ReserveRule(4.0, 1.0, 5.0)
NO_FLOOR = ReserveRule(4.0, 1.0, 0.0)
B0, B1, B2, B2_NB = ("B0 no battery", "B1 curtailment avoidance", "B2 perfect foresight",
                     "B2 no battery")
AFRR_KEYS = ["afrr_capacity_revenue", "afrr_capacity_cut", "afrr_shortfall_cost",
             "afrr_shortfall_up_mwh", "afrr_shortfall_down_mwh", "afrr_reserve_curtail_mwh"]


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


def _capacity(n: int, average_up, average_down, rejected=()) -> CapacityPrices:
    """Capacity prices on n quarter-hours of whole days from Berlin midnight, on days without
    a clock change: six 4-hour products a day, the gate at 09:00 and the award at 09:30 of
    every day but the last. The realised average is given per product, or one for all; the
    marginal is 1.5 times it, or half the bid in the products `rejected`; the bid is the same
    product's average the day before, and NaN on day 0."""
    count, step = n // 16, np.arange(n)
    block, day = step // 16, step // 96
    average = {d: np.broadcast_to(np.asarray(a, dtype=float), (count,))
               for d, a in (("up", average_up), ("down", average_down))}
    bid = {d: np.r_[np.full(6, np.nan), a[:-6]] for d, a in average.items()}
    marginal = {d: np.where(np.isin(np.arange(count), rejected), bid[d] / 2, 1.5 * a)
                for d, a in average.items()}
    gate_day = day < day[-1]
    return CapacityPrices(block=block, day=day, gate=(step % 96 == 36) & gate_day,
                          award=(step % 96 == 38) & gate_day,
                          average_up=average["up"][block], average_down=average["down"][block],
                          marginal_up=marginal["up"][block],
                          marginal_down=marginal["down"][block],
                          bid_up=bid["up"][block], bid_down=bid["down"][block])


def _afrr_run(basis: str = "bid", conn: float = 60.0, **market) -> RunConfig:
    """The default plant behind `conn` MW, with aFRR on and B2 paid on `basis`."""
    return RunConfig(dt=DT, plant=PlantConfig(conn_mw=conn),
                     market=MarketConfig(afrr_enabled=True, afrr_price_basis=basis, **market))


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
    steps differ from the model's, and a window whose price changes within a product."""
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
    with pytest.raises(ValueError, match="one price per product"):
        ReserveSpec.decide_all(block, np.array([1.0, 2.0, 3.0, 3.0]), np.ones(4), RUN,
                               integer=False, floor_mwh=0.0, margin=0.0)


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


# ------------------------------------------------------------------ B2 with aFRR (C6)
@pytest.fixture(scope="module")
def two_days():
    """Two days settled as bid, with aFRR and without, and B2's plan with its spec.

    Positive reserve pays 60-120 EUR/MW/h and negative 5-30, so B2 reserves both ways on the
    second day; products 7 and 10 reject their bids, and the first day has none.
    """
    rng = np.random.default_rng(5)
    wind, pv, price = _window(192)
    prices = _capacity(192, rng.uniform(60, 120, 12), rng.uniform(5, 30, 12), rejected=(7, 10))
    plans = []

    def recorded(*args, **kwargs):
        plan = solve(*args, **kwargs)
        plans.append((args[6], plan))
        return plan

    solve = afrr_rungs.solve_window_reserve
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(afrr_rungs, "solve_window_reserve", recorded)
        on = run_ladder(wind, pv, price, _afrr_run(), 8.0, include_b3=False,
                        include_b2_no_battery=True, afrr=prices)
    off = run_ladder(wind, pv, price, RUN, 8.0, include_b3=False, include_b2_no_battery=True)
    assert len(plans) == 1, "B2 alone plans aFRR"
    return {"wind": wind, "pv": pv, "prices": prices, "on": on, "off": off,
            "spec": plans[0][0], "plan": plans[0][1]}


def _night_and_day():
    """Two days of PV alone, a flat energy price of 50 EUR/MWh, and negative reserve paid
    30 EUR/MW/h on the "average" basis: at night only the battery's discharge can carry it."""
    n = 192
    h = np.arange(n) * DT % 24
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    return np.zeros(n), pv, np.full(n, 50.0), _capacity(n, 0.0, 30.0)


def test_b2_with_afrr_reserves_and_earns_when_capacity_pays(two_days):
    """Where capacity pays, B2 reserves both ways and earns more than without aFRR: its
    capacity revenue more than pays for the energy it no longer trades. Every rung of the run
    is settled with the six aFRR terms; B0, B1 and B2 without a battery hold nothing, so they
    earn 0 on them and their energy terms are those of the run without aFRR."""
    on, off = two_days["on"], two_days["off"]
    b2_on, b2_off = on[B2]["cost"], off[B2]["cost"]

    assert on[B2]["afrr_up"].max() > 1.0 and on[B2]["afrr_down"].max() > 1.0
    assert b2_on["afrr_capacity_revenue"] > b2_on["net_revenue"] - b2_off["net_revenue"] > 100.0
    for name in (B0, B1, B2_NB):
        assert list(on[name]["cost"]) == list(b2_on), name
        assert [on[name]["cost"][key] for key in AFRR_KEYS] == [0.0] * 6, name
        assert {key: on[name]["cost"][key] for key in off[name]["cost"]} == off[name]["cost"]
    assert all(sum(r["violations"].values()) == 0 for r in on.values())


@pytest.mark.parametrize("conn", [20.0, 30.0, 60.0])
def test_b2_with_afrr_never_earns_less_than_b2_without(conn):
    """B2 may reserve nothing, which is today's plan, so with aFRR it never earns less than
    without, within the ladder's tolerance: on three days and capacity prices each, at
    three connections."""
    for seed in range(3):
        rng = np.random.default_rng(seed)
        wind, pv, price = _window(96, seed=seed)
        prices = _capacity(96, rng.uniform(0, 40, 6), rng.uniform(0, 40, 6))
        on = run_ladder(wind, pv, price, _afrr_run("average", conn), 8.0, include_b3=False,
                        afrr=prices)[B2]["cost"]["net_revenue"]
        off = run_ladder(wind, pv, price, RunConfig(dt=DT, plant=PlantConfig(conn_mw=conn)),
                         8.0, include_b3=False)[B2]["cost"]["net_revenue"]
        assert on >= off - ladder_tolerance(off), seed


def test_b2_reserves_nothing_where_capacity_pays_nothing(two_days):
    """B2 reserves nothing in a product that pays nothing. Paid as bid: not on the first day,
    which has no bid, nor in the two products whose bid was rejected, though it reserves
    elsewhere. At a price of 0 everywhere it plans exactly as without aFRR."""
    b2, prices = two_days["on"][B2], two_days["prices"]
    unpaid = (prices.day == 0) | np.isin(prices.block, (7, 10))

    assert not np.any(b2["afrr_up"][unpaid]) and not np.any(b2["afrr_down"][unpaid])
    assert b2["afrr_up"][~unpaid].max() > 1.0

    wind, pv, price = _window(96, seed=1)
    on = run_ladder(wind, pv, price, _afrr_run("average"), 8.0, include_b3=False,
                    afrr=_capacity(96, 0.0, 0.0))[B2]
    off = run_ladder(wind, pv, price, RUN, 8.0, include_b3=False)[B2]
    assert not np.any(on["afrr_up"]) and not np.any(on["afrr_down"])
    assert on["cost"]["net_revenue"] == pytest.approx(off["cost"]["net_revenue"],
                                                      abs=ladder_tolerance(
                                                          off["cost"]["net_revenue"]))


def test_b2_objective_equals_its_settled_revenue(two_days, monkeypatch):
    """B2's LP objective is what the settlement pays its replay, capacity included, at
    rel 1e-6: its plan holds every reservation, so it earns what it planned. Also on the
    25-hour day of October, whose first product holds 20 quarter-hours and pays 5 hours."""
    plan, cost = two_days["plan"], two_days["on"][B2]["cost"]
    assert cost["afrr_capacity_revenue"] > 0
    assert plan.objective == pytest.approx(cost["net_revenue"], rel=1e-6)

    steps = [20, 16, 16, 16, 16, 16]
    n, block = sum(steps), np.repeat(np.arange(6), steps)
    rng = np.random.default_rng(8)
    up, down, never = rng.uniform(10, 40, 6)[block], rng.uniform(5, 30, 6)[block], np.zeros(n)
    prices = CapacityPrices(block=block, day=never.astype(int), gate=never > 0,
                            award=never > 0, average_up=up, average_down=down,
                            marginal_up=1.5 * up, marginal_down=1.5 * down,
                            bid_up=never * np.nan, bid_down=never * np.nan)
    solve, plans = afrr_rungs.solve_window_reserve, []

    def recorded(*args, **kwargs):
        plans.append(solve(*args, **kwargs))
        return plans[-1]

    monkeypatch.setattr(afrr_rungs, "solve_window_reserve", recorded)
    wind, pv, price = _window(n, seed=3)
    cost = run_ladder(wind, pv, price, _afrr_run("average"), 8.0, include_b3=False,
                      afrr=prices)[B2]["cost"]
    assert np.any(plans[0].r_up[:20] + plans[0].r_down[:20] > 1.0), "it reserves in 0-4"
    assert plans[0].objective == pytest.approx(cost["net_revenue"], rel=1e-6)


def test_reservations_are_constant_within_each_product(two_days):
    """A product is sold as one quantity for all its hours: one value per product and
    direction, in B2's plan and in its replay."""
    b2, block = two_days["on"][B2], two_days["prices"].block
    for reserved in (b2["afrr_up"], b2["afrr_down"]):
        assert all(np.ptp(reserved[block == b]) == 0 for b in np.unique(block))


def test_b2_holds_both_bands_and_the_headroom(two_days):
    """B2's replay holds what it sold: at both ends of every step, 4 hours of its positive
    reservation in store and 1 hour of its negative one as room; at every step, power
    headroom both ways, connection headroom for positive reserve, and export of at least the
    negative reservation. No reserve is left unheld and no limit is broken."""
    b2 = two_days["on"][B2]
    up, down = b2["afrr_up"], b2["afrr_down"]
    tol = 1e-6

    for soc in (np.r_[PLANT.soc_min, b2["soc"][:-1]], b2["soc"]):
        assert np.all(soc >= PLANT.soc_min + up * 4.0 / PLANT.eta_d - tol)
        assert np.all(soc <= PLANT.soc_max - down * 1.0 * PLANT.eta_c + tol)
    assert np.all(b2["p_bat"] + up <= PLANT.bess_mw + tol)
    assert np.all(down - b2["p_bat"] <= PLANT.bess_mw + tol)
    assert np.all(b2["export"] + up <= PLANT.conn_mw + tol)
    assert np.all(b2["export"] >= down - tol)
    assert not np.any(b2["afrr_deficit_up"]) and not np.any(b2["afrr_deficit_down"])
    assert sum(b2["violations"].values()) == 0


def test_tight_connection_suppresses_upward_reserve():
    """Generation always 1.2 x a 20 MW connection, energy at 100 EUR/MWh and capacity at
    20 EUR/MW/h: a positive reservation would keep its MW free below the connection, which
    costs exported energy worth more than it earns, so B2 holds none. Negative reserve needs
    only the export that flows anyway, so B2 holds it."""
    n = 96
    res = run_ladder(np.full(n, 24.0), np.zeros(n), np.full(n, 100.0), _afrr_run("average", 20.0),
                     0.0, include_b3=False, afrr=_capacity(n, 20.0, 20.0))[B2]
    assert np.all(res["afrr_up"] < 1e-9)
    assert res["afrr_down"].min() > 19.0


def test_b2_replay_holds_negative_reserve_when_the_battery_ends_at_its_minimum():
    """At night B2 carries negative reserve on its own discharge, exporting r x (1 + margin),
    and plans to reach its minimum state of charge in a reserved step. Replayed, its state
    of charge drifts from the plan's by solver residues, and at the minimum that drift cuts
    the export; the margin covers it, as at least half of it is left there, so every
    reservation is held. The energy price is flat, so which steps reach the minimum depends
    on the optimal vertex the solver picks: these checks hold on any of them. Without the
    margin the export at those steps falls to r."""
    wind, pv, price, prices = _night_and_day()
    b2 = run_ladder(wind, pv, price, _afrr_run("average"), 0.0, include_b3=False,
                    afrr=prices)[B2]
    down = b2["afrr_down"]
    at_minimum = (down > 1.0) & (b2["soc"] <= PLANT.soc_min + 1e-6)

    assert np.any(at_minimum & (b2["export"] <= down * (1 + 2 * PLAN_MARGIN)))
    assert np.all(b2["export"][at_minimum] >= down[at_minimum] * (1 + PLAN_MARGIN / 2))
    assert not np.any(b2["afrr_deficit_down"]) and not np.any(b2["afrr_deficit_up"])


def test_b2_refuses_a_replay_that_leaves_reserve_unheld(monkeypatch):
    """B2's reservations are hard constraints, so reserve its replay leaves unheld is a
    solver residue, not a shortfall to settle: the run stops and names the step and the
    residue. B2's plan is given 2e-4 MW, twice plant.TOL, too little export at a reserved
    night step."""
    wind, pv, price, prices = _night_and_day()
    solve, step = afrr_rungs.solve_window_reserve, []

    def short_by_a_residue(*args, **kwargs):
        plan = solve(*args, **kwargs)
        t = int(np.flatnonzero((plan.r_down > 1.0) & (pv == 0.0))[0])
        step.append(t)
        p_bat = plan.p_bat.copy()
        p_bat[t] = plan.r_down[t] - 2e-4
        return plan._replace(p_bat=p_bat)

    monkeypatch.setattr(afrr_rungs, "solve_window_reserve", short_by_a_residue)
    with pytest.raises(RuntimeError, match=r"holds 0\.0002 MW too little of its") as raised:
        run_ladder(wind, pv, price, _afrr_run("average"), 0.0, include_b3=False, afrr=prices)
    assert f"at step {step[0]} (product {step[0] // 16})" in str(raised.value)


def test_rounding_b2_down_keeps_its_plan_feasible(two_days):
    """Rounded down to whole MW, and to 0 where under 1 MW or where its state of charge
    leaves no room for the floor, B2's reservations hold under the floor with B2's own
    schedule: replayed with them, nothing is left unheld. Here the floor drops a negative
    reservation of about 3 MW. The rounding bound is the capacity revenue that loses."""
    spec, plan = two_days["spec"], two_days["plan"]
    floor = MarketConfig().afrr_floor_hours * PLANT.bess_mw
    up, down = whole_megawatts(plan, spec, PLANT.soc_min, floor, PLANT)
    res = simulate(two_days["wind"], two_days["pv"], plan.p_bat, plan.curt, DT, PLANT,
                   reserve_up=up, reserve_down=down, rule=RULE)

    assert not np.any(res["afrr_deficit_up"]) and not np.any(res["afrr_deficit_down"])
    for whole, planned in ((up, plan.r_up), (down, plan.r_down)):
        assert np.all(whole == np.floor(whole)) and np.all(whole <= planned)
        assert np.all((whole == 0) | (whole >= 1))
    assert np.any((plan.r_down >= 1.0) & (down == 0)), "the floor drops one"
    paid_up, paid_down = paid_prices(two_days["prices"], "bid")
    lost = np.sum(paid_up * (plan.r_up - up) + paid_down * (plan.r_down - down)) * DT
    assert two_days["on"][B2]["afrr_rounding_bound_eur"] == pytest.approx(lost, rel=1e-12)
    assert lost > 0


def test_rounding_drops_a_reservation_that_misses_the_floor_at_any_instant():
    """Rounding reads the plan's state of charge at both ends of every step of a product.
    Four products of 3, 1, 5 and 1 hours, as on the 23- and 25-hour days, paid 10 EUR/MW/h
    up and 20 down: 1.6 MW up at 7 MWh hold their band as 1 MW (6.21 MWh) but not the floor
    (7.26 MWh), so 0; 3.4 MW down keep room for the floor (33.25 MWh) at each of their own
    steps' ends, but not at the start of the first, so 0; and 2.5 MW up with 3.4 MW down at
    33 MWh become 2 and 3 MW. The capacity lost: 10 x 1.6 x 3 + 20 x 3.4 x 5
    + (10 x 0.5 + 20 x 0.4) x 1 = 401 EUR."""
    steps = [12, 4, 20, 4]
    block = np.repeat(np.arange(4), steps)
    soc = np.repeat([7.0, 34.0, 33.0, 33.0], steps)
    r_up = np.repeat([1.6, 0.0, 0.0, 2.5], steps)
    r_down = np.repeat([0.0, 0.0, 3.4, 3.4], steps)
    spec = _spec(block, fixed_up=np.nan, fixed_down=np.nan, paid_up=10.0, paid_down=20.0,
                 rule=NO_FLOOR)
    plan = ReservePlan(np.zeros(len(block)), np.zeros(len(block)), r_up, r_down, soc, 0.0)

    up, down = whole_megawatts(plan, spec, 7.0, 5.0, PLANT)

    assert up.tolist() == np.repeat([0.0, 0.0, 0.0, 2.0], steps).tolist()
    assert down.tolist() == np.repeat([0.0, 0.0, 0.0, 3.0], steps).tolist()
    assert rounding_bound(plan, spec, 7.0, 5.0, PLANT) == pytest.approx(401.0, rel=1e-12)


def test_the_rounding_bound_brackets_the_integer_optimum():
    """On a day, the best plan in whole MW under the floor earns at most B2's relaxation, and
    at least the relaxation less its rounding bound. Both are solved with the same margin;
    the gap between them is real, so a bound of 0 would fail."""
    wind, pv, price = _window(96, seed=5)
    rng = np.random.default_rng(6)
    prices = _capacity(96, rng.uniform(5, 30, 6), rng.uniform(5, 30, 6))
    run = _afrr_run("average")
    eff = effective_price(price, run.market, 8.0, DT)
    paid_up, paid_down = paid_prices(prices, "average")
    floor = run.market.afrr_floor_hours * PLANT.bess_mw

    def best(integer, floor_mwh):
        spec = ReserveSpec.decide_all(prices.block, paid_up, paid_down, run, integer=integer,
                                      floor_mwh=floor_mwh, margin=PLAN_MARGIN)
        return spec, solve_window_reserve(wind, pv, eff, DT, run, PLANT.soc_min, spec)

    spec, relaxation = best(False, 0.0)
    _, whole = best(True, floor)
    bound = rounding_bound(relaxation, spec, PLANT.soc_min, floor, PLANT)
    tol = 1e-6 * abs(relaxation.objective)

    assert relaxation.objective - bound <= whole.objective + tol
    assert whole.objective <= relaxation.objective + tol
    assert whole.objective < relaxation.objective - 1.0


def test_afrr_refuses_what_it_cannot_settle():
    """A ladder with aFRR refuses: capacity prices with the switch off, the switch on
    without them, prices for another window; an export cap and grid charging, which the
    model does not bound for negative reserve; a connection below the battery's rating;
    B3 on the "average" basis, a bid nobody knows at the gate; and a penalty at or below a
    price paid in the window. Just above that price it runs."""
    wind, pv, price = _window(96)
    prices = _capacity(96, 10.0, 10.0)                  # the marginal, 15 EUR/MW/h, is paid most
    on = _afrr_run()
    cases = [
        (RUN, prices, {}, "afrr_enabled is False and afrr= is given"),
        (on, None, {}, "afrr_enabled is True and afrr= is missing"),
        (on, _capacity(192, 10.0, 10.0), {}, "afrr= holds 192 steps and the window 96"),
        (on, prices, {"export_cap": np.full(96, 40.0)}, "export cap and grid charging"),
        (on.with_(plant=PlantConfig(grid_charging_allowed=True)), prices, {},
         "export cap and grid charging"),
        (on.with_(plant=PlantConfig(conn_mw=15.0)), prices, {}, "at least the battery's 20"),
        (_afrr_run("average"), prices, {"include_b3": True}, "cannot be paid the realised"),
        (_afrr_run("marginal", afrr_shortfall_penalty_eur_mwh=15.0), prices, {},
         r"penalty_eur_mwh \(15\.00\) must exceed"),
        (_afrr_run("marginal", afrr_shortfall_penalty_eur_mwh=12.0), prices, {},
         (r"penalty_eur_mwh \(12\.00\) must exceed every capacity price paid in the window "
          r"\(15\.00 EUR/MW/h\)")),
    ]
    for run, afrr, kwargs, message in cases:
        with pytest.raises(ValueError, match=message):
            run_ladder(wind, pv, price, run, 8.0, **{"include_b3": False, **kwargs},
                       afrr=afrr)
    above = _afrr_run("marginal", afrr_shortfall_penalty_eur_mwh=15.01)
    assert run_ladder(wind, pv, price, above, 8.0, include_b3=False,
                      afrr=prices)[B2]["cost"]["afrr_capacity_revenue"] > 0


def test_afrr_refuses_b3_until_its_bids_are_built():
    """B3 does not bid aFRR capacity yet, so a ladder with aFRR refuses to run it, rather
    than run it without reserve beside a B2 that reserves. Replaced by B3's tests once it
    bids."""
    wind, pv, price = _window(96)
    with pytest.raises(NotImplementedError, match="B3 does not bid aFRR capacity yet"):
        run_ladder(wind, pv, price, _afrr_run(), 8.0, include_b3=True,
                   afrr=_capacity(96, 10.0, 10.0))


def test_the_ladder_allows_a_rung_the_margin_on_its_capacity_revenue(monkeypatch):
    """B2 holds its reservations with PLAN_MARGIN to spare, so the invariant lets a rung
    out-earn it by that share of the rung's own capacity revenue, beyond the usual
    tolerance, and by no more. B1 is replaced by B2's own dispatch holding every reservation
    PLAN_MARGIN / 2 larger: that passes, though it out-earns B2 by more than the usual
    tolerance. Twice PLAN_MARGIN larger stops the run."""
    wind, pv, price = _window(96, seed=2)
    run, prices = _afrr_run("average"), _capacity(96, 40.0, 20.0)
    eff = effective_price(price, run.market, 8.0, DT)

    def holding_more(share):
        def b1(wind, pv, run, export_cap=None):
            res = afrr_rungs.b2_reserve(wind, pv, eff, run, prices)
            scale = 1.0 + share * PLAN_MARGIN
            return {**res, "afrr_up": res["afrr_up"] * scale,
                    "afrr_down": res["afrr_down"] * scale}
        return b1

    monkeypatch.setattr(baselines, "b1_curtailment_avoidance", holding_more(0.5))
    res = run_ladder(wind, pv, price, run, 8.0, include_b3=False, afrr=prices)
    b2 = res[B2]["cost"]["net_revenue"]
    assert res[B1]["cost"]["net_revenue"] - b2 > ladder_tolerance(b2)

    monkeypatch.setattr(baselines, "b1_curtailment_avoidance", holding_more(2.0))
    with pytest.raises(AssertionError, match="ladder invariant violated"):
        run_ladder(wind, pv, price, run, 8.0, include_b3=False, afrr=prices)

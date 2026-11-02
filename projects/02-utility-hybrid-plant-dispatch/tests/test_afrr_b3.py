"""B3 with aFRR: its bids at the 09:00 gate, the 09:30 award, and its re-plans around them.

The battery is the default one: 20 MW, 40 MWh, a state of charge from 2 to 38 MWh and
efficiencies of 0.95. Every test here fails on c7b98ed, where a ladder with aFRR refuses B3
and b3_rolling takes no capacity prices.
"""
from __future__ import annotations

import numpy as np
import pytest

from hybrid import afrr_rungs, baselines, plant
from hybrid.baselines import b3_rolling, ladder_tolerance, make_forecasts, run_ladder
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.market import effective_price, premium_eligible
from hybrid.plant import dispatch_step, simulate
from hybrid.reserve import PLAN_MARGIN, CapacityPrices, ReserveRule, paid_prices

DT = 0.25
PLANT = PlantConfig()
B2, B3 = "B2 perfect foresight", "B3 rolling MPC"
# what a replay must reproduce of what B3 executed, step for step
DISPATCH = ("export", "import", "gen", "p_bat", "chosen_curtail", "forced_curtail", "soc",
            "reserve_curtail", "afrr_deficit_up", "afrr_deficit_down")
GATE, AWARD = 36, 38                 # 09:00 and 09:30 of a window's first day
# B3's energy rule: 4 hours up, 1 hour down, and the floor of 15 minutes of 20 MW
RULE = ReserveRule(4.0, 1.0, 5.0)


def _window(n: int, seed: int = 0):
    """Wind and PV in MW and a day-ahead price in EUR/MWh, from midnight."""
    rng = np.random.default_rng(seed)
    h = np.arange(n) * DT % 24
    wind = np.clip(28 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 30 * (pv / 30) \
        + 15 * rng.standard_normal(n)
    return wind, pv, price


def _prices(n: int, bid_up, bid_down, start_h: int = 0, rejected=()) -> CapacityPrices:
    """Capacity prices on n quarter-hours from start_h o'clock on day 0, on days without a
    clock change: six 4-hour products a day, the gate at 09:00 and the award at 09:30 of
    each day whose next day lies in the window. bid_up, bid_down: B3's bid, EUR/MW/h, per
    product of the window in time order, or one for all; NaN on day 0 whatever is given. The
    bid is accepted, at a marginal of twice it, but in the products `rejected`, whose marginal
    is half of it; the realised average is the bid, and 10 EUR/MW/h on day 0."""
    quarter = 4 * start_h + np.arange(n)
    day, block = quarter // 96, quarter // 16 - quarter[0] // 16
    accepted = np.where(np.isin(block, rejected), 0.5, 2.0)
    fields = {}
    for name, bid in (("up", bid_up), ("down", bid_down)):
        per_product = np.broadcast_to(np.asarray(bid, dtype=float), (int(block[-1]) + 1,))
        bid = np.where(day == 0, np.nan, per_product[block])
        fields.update({f"bid_{name}": bid, f"average_{name}": np.nan_to_num(bid, nan=10.0),
                       f"marginal_{name}": accepted * np.nan_to_num(bid, nan=10.0)})
    has_next = day < day[-1]
    return CapacityPrices(block=block, day=day, gate=(quarter % 96 == 36) & has_next,
                          award=(quarter % 96 == 38) & has_next, **fields)


def _run(**market) -> RunConfig:
    """The default plant behind 60 MW, aFRR on, B2 paid as bid, B3 on a 4-hour horizon."""
    return RunConfig(dt=DT, horizon_steps=16,
                     market=MarketConfig(afrr_enabled=True, **market))


def _per_product(values: np.ndarray, prices: CapacityPrices) -> np.ndarray:
    """One value per product, its first step's, of an array that holds one per step."""
    return values[np.unique(prices.block, return_index=True)[1]]


@pytest.fixture(scope="module")
def three_days():
    """B3 with aFRR on three days, re-planning every 4 steps, with what it executed in each
    step and the reservations each plan was given recorded. Capacity pays 5-30 EUR/MW/h each
    way; products 9 and 14 reject their bids. B3 plans on a premium of 8 EUR/MWh that rises
    to 11 at step 20, so that a plan prices its horizon at its own step's rate."""
    n = 288
    wind, pv, price = _window(n)
    rng = np.random.default_rng(5)
    prices = _prices(n, rng.uniform(5, 30, 18), rng.uniform(5, 30, 18), rejected=(9, 14))
    run = _run()
    rate = np.r_[np.full(20, 8.0), np.full(n - 20, 11.0)]
    eff = effective_price(price, run.market, rate, DT)
    premium = (rate, premium_eligible(price, run.market, DT))
    forecasts = make_forecasts(wind, pv, eff, run, np.random.default_rng(1))
    executed, specs = [], []
    spec = afrr_rungs._Book.spec

    def recorded(*args):
        # dispatch_step's arguments in order: wind, pv, soc, p_bat, curtail_frac, dt, cfg,
        # export_cap, reserve
        executed.append({**dispatch_step(*args), "proposed": args[3], "curtail_frac": args[4],
                         "reserve": args[8]})
        return executed[-1]

    def recorded_spec(book, start, stop, decide=None):
        specs.append((start, stop, decide, spec(book, start, stop, decide)))
        return specs[-1][-1]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(afrr_rungs, "dispatch_step", recorded)
        patch.setattr(afrr_rungs._Book, "spec", recorded_spec)
        res = b3_rolling(wind, pv, eff, *forecasts, run, resolve_every=4, premium=premium,
                         afrr=prices)
    return {"wind": wind, "pv": pv, "eff": eff, "forecasts": forecasts, "premium": premium,
            "prices": prices, "run": run, "res": res, "executed": executed, "specs": specs}


def _rerun(case: dict, prices: CapacityPrices | None = None, truth=None) -> dict:
    """B3 on the fixture's window again, with other capacity prices or another truth
    (wind, pv, eff_price), and the fixture's forecasts and premium."""
    wind, pv, eff = truth if truth is not None else (case["wind"], case["pv"], case["eff"])
    return b3_rolling(wind, pv, eff, *case["forecasts"], case["run"], resolve_every=4,
                      premium=case["premium"], afrr=case["prices"] if prices is None else prices)


# ------------------------------------------------------------------ bids and award (C7)
def test_b3_bids_whole_megawatts_and_nothing_on_the_first_day(three_days):
    """B3 bids in whole MW, from 0 to the battery's 20, one quantity per product, and bids
    each day that has a gate the day before: days 1 and 2, both ways. The first day has no
    gate in the window, so B3 bids and holds nothing on it."""
    res, prices = three_days["res"], three_days["prices"]
    for bid, held in ((res["afrr_bid_up"], res["afrr_up"]),
                      (res["afrr_bid_down"], res["afrr_down"])):
        assert np.all(bid == np.round(bid)) and np.all((bid >= 0) & (bid <= PLANT.bess_mw))
        assert all(np.ptp(bid[prices.block == b]) == 0 for b in np.unique(prices.block))
        assert not np.any(bid[prices.day == 0]) and not np.any(held[prices.day == 0])
    for day in (1, 2):
        assert res["afrr_bid_down"][prices.day == day].max() >= 1.0, day
    assert res["afrr_bid_up"].max() >= 1.0
    assert len(res["afrr_commit_solve_time_s"]) == 2 and res["afrr_commit_failures"] == 0


def test_b3_learns_its_award_at_half_past_nine(three_days, monkeypatch):
    """B3 bids at 09:00 and learns at 09:30 which bids won. Where the next day's bids are
    rejected instead of accepted, its dispatch is the same until the award, step for step,
    and so are its bids; from the award on, a rejected product holds nothing, as B3 executes
    its steps and in its result, while the accepted ones hold what was bid."""
    case, prices = three_days, three_days["prices"]
    rejected = tuple(range(6, 12))                       # all of day 1's products
    executed = []

    def recorded(*args):
        executed.append(args[8])                         # the step's Reservation
        return dispatch_step(*args)

    monkeypatch.setattr(afrr_rungs, "dispatch_step", recorded)
    other = _rerun(case, _prices(288, prices.bid_up[::16][:18], prices.bid_down[::16][:18],
                                 rejected=rejected))
    res = case["res"]

    for key in DISPATCH:
        assert np.array_equal(other[key][:AWARD], res[key][:AWARD]), key
    day1 = prices.day == 1
    for direction in ("up", "down"):
        bid = res[f"afrr_bid_{direction}"]
        assert np.array_equal(other[f"afrr_bid_{direction}"], bid), direction
        assert not np.any(other[f"afrr_{direction}"][day1]), direction
        assert not any(getattr(executed[t], direction) for t in np.flatnonzero(day1))
        accepted = day1 & ~np.isin(prices.block, (9, 14))
        assert np.array_equal(res[f"afrr_{direction}"][accepted], bid[accepted]), direction
    assert np.any(res["afrr_down"][day1] > 0)


def test_b3_bids_read_no_realised_value_after_their_gate_except_through_the_forecasts(
        three_days):
    """What B3 bids at a gate rests on what it knows then: its state of charge, its
    forecasts and its bids' prices. Changing the true wind, PV and price and the realised
    capacity prices from the first gate on, with the forecasts and the bid prices fixed,
    leaves the next day's bids as they were. Changing the bid prices, yesterday's averages,
    changes them, so the test can see a difference."""
    case, prices = three_days, three_days["prices"]
    later = np.arange(288) >= GATE
    truth = tuple(np.where(later, 0.5 * x + 3.0, x) for x in (case["wind"], case["pv"],
                                                               case["eff"]))
    realised = {name: np.where(later, 3.0 * getattr(prices, name), getattr(prices, name))
                for name in ("average_up", "average_down", "marginal_up", "marginal_down")}
    other = _rerun(case, CapacityPrices(**{**vars(prices), **realised}), truth)
    day1 = prices.day == 1
    for direction in ("up", "down"):
        assert np.array_equal(other[f"afrr_bid_{direction}"][day1],
                              case["res"][f"afrr_bid_{direction}"][day1]), direction

    cheaper = _rerun(case, CapacityPrices(**{**vars(prices),
                                             "bid_down": 0.2 * prices.bid_down}))
    assert not np.array_equal(cheaper["afrr_bid_down"][day1],
                              case["res"]["afrr_bid_down"][day1])


def test_b3_plans_as_energy_only_b3_until_its_first_gate(three_days, monkeypatch):
    """Before its first gate B3 holds nothing, and a plan whose horizon holds nothing is
    today's plan, so B3 with aFRR dispatches as B3 without it executes, step for step, until
    then, on the same plans. (B3 without aFRR re-simulates the battery power it applied,
    which the safety layer may apply otherwise where it is proposed again.)"""
    case = three_days
    executed = []

    def recorded(*args, **kwargs):
        executed.append(dispatch_step(*args, **kwargs))
        return executed[-1]

    monkeypatch.setattr(plant, "dispatch_step", recorded)
    plain = b3_rolling(case["wind"], case["pv"], case["eff"], *case["forecasts"], case["run"],
                       resolve_every=4, premium=case["premium"])
    for key in ("export", "gen", "p_bat", "chosen_curtail", "forced_curtail", "soc"):
        # the loop's steps come first, then its result's re-simulation
        assert np.array_equal(case["res"][key][:GATE],
                              [step[key] for step in executed[:GATE]]), key
    assert np.array_equal(case["res"]["terminal_price_eur_mwh"][:GATE // 4],
                          plain["terminal_price_eur_mwh"][:GATE // 4])


def test_b3_honours_its_award_in_its_final_simulation(three_days):
    """B3's result holds what it was awarded: its bids where they were accepted, and nothing
    where they were rejected or on the first day; and its final simulation, with those
    reservations, reproduces the dispatch it executed, step for step. On this window a
    re-simulation of the battery power B3 applied would not, where it discharged into an
    export the connection had to curtail. Negative control: a re-simulation without the
    reservations, the archived project's bug, does not either."""
    case, prices = three_days, three_days["prices"]
    res, executed = case["res"], case["executed"]
    for direction in ("up", "down"):
        bid = getattr(prices, f"bid_{direction}")
        accepted = bid <= getattr(prices, f"marginal_{direction}")
        assert np.array_equal(res[f"afrr_{direction}"],
                              np.where(accepted, res[f"afrr_bid_{direction}"], 0.0))
    for key in DISPATCH:
        assert np.array_equal(res[key], [step[key] for step in executed]), key
    assert [(step["reserve"].up, step["reserve"].down) for step in executed] == list(
        zip(res["afrr_up"], res["afrr_down"]))

    proposed, curtail = ([step[key] for step in executed] for key in ("proposed", "curtail_frac"))
    unreserved = simulate(case["wind"], case["pv"], proposed, curtail, DT, PLANT)
    assert not all(np.array_equal(unreserved[key], res[key]) for key in DISPATCH[:7])
    applied = simulate(case["wind"], case["pv"], res["p_bat"], curtail, DT, PLANT,
                       reserve_up=res["afrr_up"], reserve_down=res["afrr_down"],
                       rule=RULE)
    assert not np.array_equal(applied["p_bat"], res["p_bat"])


def test_b3_reservations_are_constant_within_each_product(three_days):
    """A product is sold as one quantity for all its hours: B3 holds one value per product
    and direction, as B2 does."""
    res, block = three_days["res"], three_days["prices"].block
    for held in (res["afrr_up"], res["afrr_down"]):
        assert all(np.ptp(held[block == b]) == 0 for b in np.unique(block))


def test_b3_with_afrr_stays_below_b2_with_afrr():
    """B2 is paid the same bids, with perfect foresight and continuous MW, so B3 with aFRR
    does not out-earn it beyond the ladder's tolerance and the share of B3's own capacity
    revenue that B2's planning margin may cost B2: the ladder runs on three days without
    stopping, B3 reserves, and it is settled on its accepted bids."""
    n = 288
    wind, pv, price = _window(n, seed=3)
    rng = np.random.default_rng(7)
    prices = _prices(n, rng.uniform(5, 30, 18), rng.uniform(5, 30, 18), rejected=(8, 15))
    res = run_ladder(wind, pv, price, _run(), 8.0, include_b3=True, afrr=prices)
    b2, b3 = (res[name]["cost"] for name in (B2, B3))
    before = {name: c["net_revenue"] + c["imbalance_cost"] for name, c in ((B2, b2), (B3, b3))}

    allowed = ladder_tolerance(before[B2]) + PLAN_MARGIN * b3["afrr_capacity_revenue"]
    assert before[B3] <= before[B2] + allowed
    assert res[B3]["afrr_up"].max() + res[B3]["afrr_down"].max() >= 1.0
    paid_up, paid_down = paid_prices(prices, "bid")
    assert b3["afrr_capacity_revenue"] == pytest.approx(
        np.sum(paid_up * res[B3]["afrr_up"] + paid_down * res[B3]["afrr_down"]) * DT, rel=1e-12)
    assert sum(res[B3]["violations"].values()) == 0


def test_b3_is_paid_its_accepted_bids_whatever_b2_is_paid(monkeypatch):
    """B3 bids, so the settlement pays it its accepted bids, also where B2 is paid the
    realised marginal: B3 is replaced by B0's dispatch holding 5 MW up and 3 MW down after
    the first day, and its capacity revenue is what its bids pay for them, not the marginal."""
    n = 192
    wind, pv, price = _window(n, seed=1)
    prices = _prices(n, 12.0, 8.0, rejected=(7,))

    def holding(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, export_cap=None,
                progress=False, premium=None, afrr=None):
        held = (afrr.day > 0).astype(float)
        return {**baselines.b0_no_battery(wind, pv, run), "afrr_up": 5.0 * held,
                "afrr_down": 3.0 * held}

    monkeypatch.setattr(baselines, "b3_rolling", holding)
    res = run_ladder(wind, pv, price, _run(afrr_price_basis="marginal"), 8.0, include_b3=True,
                     afrr=prices)
    paid_up, paid_down = paid_prices(prices, "bid")
    expected = np.sum(5.0 * paid_up[prices.day > 0] + 3.0 * paid_down[prices.day > 0]) * DT

    assert res[B3]["cost"]["afrr_capacity_revenue"] == pytest.approx(expected, rel=1e-12)
    assert expected == pytest.approx((5 * 12 + 3 * 8) * 20.0, rel=1e-12)     # 5 of 6 products
    assert res[B2]["cost"]["afrr_capacity_revenue"] > 0


def _gate_window(days: int, wind: float, price, bid_up, bid_down, **market):
    """A window that starts at a gate, 09:00 on day 0, and runs to the end of day `days`:
    constant wind behind 60 MW and no PV, at `price` per step (one for all, or per step),
    forecast as they are. B3 re-plans only where it must, at the gates and the awards, so the
    window is quick to run; it is for tests of what B3 bids. Returns (the arguments of
    b3_rolling up to the run's settings, the window's prices)."""
    n = 60 + 96 * days
    eff = np.broadcast_to(np.asarray(price, dtype=float), (n,)).copy()
    prices = _prices(n, bid_up, bid_down, start_h=9)
    gen = (np.full(n, float(wind)), np.zeros(n))
    return (*gen, eff, *gen, eff, _run(**market)), prices


def _gate_plans(monkeypatch) -> list:
    """Every plan made from now on, with its spec: (spec, plan) pairs; a gate's spec decides
    in whole MW."""
    solve, plans = afrr_rungs.solve_window_reserve, []

    def recorded(*args, **kwargs):
        plans.append((args[6], solve(*args, **kwargs)))
        return plans[-1][1]

    monkeypatch.setattr(afrr_rungs, "solve_window_reserve", recorded)
    return plans


def test_b3_values_holding_reserve_into_the_evening(monkeypatch):
    """The gate plans to the end of the day after the one it bids for, so energy stored at
    midnight keeps its value. With constant wind, energy and capacity prices, the next day's
    six products are alike, and B3 bids the same positive reserve in 20-24 as in 16-20.
    Negative control: planned only to the end of the day it bids for, the positive band of
    20-24 would hold energy the plan can never sell, so B3 bids less there."""
    args, prices = _gate_window(2, 30.0, 50.0, 20.0, 5.0)
    day1 = _per_product(prices.day == 1, prices)

    def bids_up():
        res = b3_rolling(*args, resolve_every=10 ** 6, afrr=prices)
        return _per_product(res["afrr_bid_up"], prices)[day1]

    whole = bids_up()
    assert whole[5] == whole[4] >= 1.0
    assert np.all(whole == whole[0])
    monkeypatch.setattr(afrr_rungs, "_GATE_DAYS", 1)
    short = bids_up()
    assert short[5] < short[4]


def test_the_gate_keeps_the_floor_for_a_small_reservation(monkeypatch):
    """A whole-MW bid keeps the floor of 15 minutes of the battery's 20 MW as well as its
    band. Started at 36 MWh, with 3 MW of wind and energy worth 10 EUR/MWh until 20:00 on the
    day it bids for and 300 EUR/MWh after, the gate keeps its energy to sell late; its
    negative reserve, paid 30 EUR/MW/h until 20:00, is carried by the wind alone, a few MW.
    Each such bid keeps room for the floor, 5 MWh x 0.95 below the 38 MWh maximum, at every
    step boundary of its product, and the plan sits on that limit. Negative control: without
    the floor the plan keeps more energy, which the floor would not allow."""
    hour = (9 + np.arange(60 + 96) / 4) % 24
    late = (np.arange(60 + 96) >= 60) & (hour >= 20)
    bid_down = np.r_[np.zeros(4), np.full(5, 30.0), 0.0]     # day 0: 08-24; day 1: 00-24
    ceiling = PLANT.soc_max - 5.0 * PLANT.eta_c
    start = PlantConfig(soc_init_frac=0.9)

    def gate(floor_hours):
        plans = _gate_plans(monkeypatch)
        args, prices = _gate_window(1, 3.0, np.where(late, 300.0, 10.0), 0.0, bid_down,
                                    afrr_floor_hours=floor_hours)
        b3_rolling(*args[:-1], args[-1].with_(plant=start), resolve_every=10 ** 6,
                   afrr=prices)
        plan = next(plan for spec, plan in plans if spec.integer)
        reserved = plan.r_down > 0
        highest = np.maximum(np.r_[start.soc_init, plan.soc[:-1]], plan.soc)
        return plan.r_down[reserved], highest[reserved]

    bids, highest = gate(0.25)
    assert len(bids) and np.all((bids >= 1.0) & (bids <= 4.0))
    assert np.all(highest <= ceiling + 1e-6)
    assert highest.max() == pytest.approx(ceiling, abs=1e-6)
    _, unfloored = gate(0.0)
    assert unfloored.max() > ceiling + 0.5


def test_a_gate_whose_plan_is_not_proven_optimal_bids_nothing_and_is_counted(monkeypatch):
    """A gate whose plan is not solved to optimality bids nothing for the next day, and the
    result counts it, so that a failure is reported rather than hidden; B3 runs on."""
    solve = afrr_rungs.solve_window_reserve

    def failing_gates(*args, **kwargs):
        return None if args[6].integer else solve(*args, **kwargs)

    monkeypatch.setattr(afrr_rungs, "solve_window_reserve", failing_gates)
    args, prices = _gate_window(2, 30.0, 50.0, 20.0, 5.0)
    res = b3_rolling(*args, resolve_every=10 ** 6, afrr=prices)

    assert res["afrr_commit_failures"] == 2 and len(res["afrr_commit_solve_time_s"]) == 2
    assert not np.any(res["afrr_bid_up"]) and not np.any(res["afrr_bid_down"])
    assert not np.any(res["afrr_up"]) and not np.any(res["afrr_down"])


def test_b3_refuses_to_bid_for_a_day_without_bid_prices():
    """B3 bids each product at its bid price, so a day it bids for must have one: a missing
    bid price for the next day stops the run, naming the day."""
    args, prices = _gate_window(1, 30.0, 50.0, 20.0, 5.0)
    unpriced = CapacityPrices(**{**vars(prices), "bid_up": np.full(len(prices.block), np.nan)})
    with pytest.raises(ValueError, match="no bid price for day 1 of the window"):
        b3_rolling(*args, resolve_every=10 ** 6, afrr=unpriced)


def test_b3_re_plans_fix_what_it_holds_at_its_bids(three_days):
    """Every re-plan whose horizon holds reserve fixes each product there at what B3 holds,
    priced at its bid (nothing on the first day, which has none) and charged for the
    product's 4 hours; every other re-plan is today's plan, on a horizon that holds nothing.
    Only the two gates decide."""
    case = three_days
    res, prices, run = case["res"], case["prices"], case["run"]
    gates = [entry for entry in case["specs"] if entry[2] is not None]
    plans = {start: (stop, spec) for start, stop, decide, spec in case["specs"]
             if decide is None}
    fresh = [t for t in range(288) if t % 4 == 0 or prices.gate[t] or prices.award[t]]

    assert [start for start, *_ in gates] == list(np.flatnonzero(prices.gate))
    assert set(plans) < set(fresh) and len(plans) > 10
    for t in fresh:
        stop = min(t + run.horizon_steps, 288)
        held = (res["afrr_up"][t:stop], res["afrr_down"][t:stop])
        if t not in plans:
            assert not np.any(held[0]) and not np.any(held[1]), t
            continue
        assert plans[t][0] == stop
        spec = plans[t][1]
        assert np.array_equal(spec.fixed_up[spec.block], held[0]), t
        assert np.array_equal(spec.fixed_down[spec.block], held[1]), t
        assert np.array_equal(spec.paid_up[spec.block], np.nan_to_num(prices.bid_up[t:stop]))
        assert np.all(spec.hours == 4.0) and not spec.integer and spec.margin == 0.0


def test_b3_holds_its_bids_as_if_accepted_until_the_award(monkeypatch):
    """Between the gate and the award B3 does not know which bids won, so its re-plans hold
    every bid as if accepted; from the award on, a rejected bid holds nothing. On a 24-hour
    horizon the gate's own re-plan, at 09:00, sees the next day's first products: it holds
    the 00-04 bid, which is rejected, and the re-plan at 09:30 holds 0 there. B3 re-plans at
    each gate and award, whatever its resolve_every: here only there."""
    plans = _gate_plans(monkeypatch)
    args, prices = _gate_window(2, 30.0, 50.0, 20.0, 5.0)
    prices = _prices(len(prices.block), 20.0, 5.0, start_h=9, rejected=(4,))   # day 1, 00-04
    res = b3_rolling(*args[:-1], args[-1].with_(horizon_steps=96), resolve_every=10 ** 6,
                     afrr=prices)
    replans = [spec for spec, plan in plans if not spec.integer]
    gate, award = replans[:2]
    first = 60                                          # day 1, 00:00, in product 4 of 0-6

    assert len(replans) == 4                            # at 09:00 and 09:30 on days 0 and 1
    assert res["afrr_bid_up"][first] >= 1.0 and res["afrr_bid_down"][first] >= 1.0
    assert gate.fixed_up[4] == res["afrr_bid_up"][first]
    assert gate.fixed_down[4] == res["afrr_bid_down"][first]
    assert award.fixed_up[4] == award.fixed_down[4] == 0.0
    assert award.fixed_up[5] == res["afrr_up"][first + 16] == res["afrr_bid_up"][first + 16]


def test_b3_bids_on_the_premium_it_knows_at_its_gate(monkeypatch):
    """A gate prices its whole plan at the premium of the gate step, as B3's re-plans price
    theirs: a premium that rises by 40 EUR/MWh from the next day on, which B3 cannot know at
    the gate, though its forecasts carry it, leaves its bids as they were. Negative control:
    priced at each step's own rate, the gate bids otherwise."""
    args, prices = _gate_window(2, 30.0, 50.0, 20.0, 5.0)
    wind, pv, eff, wind_fc, pv_fc, price_fc, run = args
    n, day1 = len(prices.block), _per_product(prices.day == 1, prices)
    rate, payable = np.full(n, 8.0), np.ones(n, dtype=bool)
    later = np.where(np.arange(n) >= 60, 48.0, 8.0)

    def bids(premium_rate):
        rise = premium_rate - rate
        res = b3_rolling(wind, pv, eff + rise, wind_fc, pv_fc, price_fc + rise, run,
                         resolve_every=10 ** 6, premium=(premium_rate, payable), afrr=prices)
        return [_per_product(res[f"afrr_bid_{d}"], prices)[day1] for d in ("up", "down")]

    known = bids(rate)
    assert all(np.array_equal(a, b) for a, b in zip(bids(later), known))
    monkeypatch.setattr(afrr_rungs, "_planning_price", lambda price, *args: price)
    assert not all(np.array_equal(a, b) for a, b in zip(bids(later), known))

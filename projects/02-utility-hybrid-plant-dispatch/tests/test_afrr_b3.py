"""B3 with aFRR: its bids at the 09:00 gate, the 09:30 award, its re-plans around them, and
the rules that keep its awards through forecast errors.

The battery is the default one: 20 MW, 40 MWh, a state of charge from 2 to 38 MWh and
efficiencies of 0.95. Every test here fails on c7b98ed, where a ladder with aFRR refuses B3
and b3_rolling takes no capacity prices; those of B3's rules fail on 4eb0fee, whose B3 has
no derating, curtails at a non-negative price only through the safety layer, and sinks no
deficit. Tests added in review pin rules the first ones left unchecked, and one, B3 dipping
below its positive band to keep its negative reserve, fails on 980b3b8, whose safety layer
refused that dip.
"""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hybrid import afrr_rungs, baselines, plant
from hybrid.afrr_data import capacity_prices, parse_results
from hybrid.baselines import b3_rolling, ladder_tolerance, make_forecasts, run_ladder
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.market import effective_price, premium_eligible
from hybrid.plant import dispatch_step, simulate
from hybrid.reserve import (PLAN_MARGIN, CapacityPrices, ReserveRule, ReserveSpec,
                            add_reserve_terms, paid_prices)

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


def _prices(n: int, bid_up, bid_down, start_h: int = 0, rejected=(),
            tied=()) -> CapacityPrices:
    """Capacity prices on n quarter-hours from start_h o'clock on day 0, on days without a
    clock change: six 4-hour products a day, the gate at 09:00 and the award at 09:30 of
    each day whose next day lies in the window. bid_up, bid_down: B3's bid, EUR/MW/h, per
    product of the window in time order, or one for all; NaN on day 0 whatever is given. The
    bid is accepted, at a marginal of twice it, but in the products `rejected`, whose marginal
    is half of it, and in the products `tied`, whose marginal is the bid; the realised
    average is the bid, and 10 EUR/MW/h on day 0."""
    quarter = 4 * start_h + np.arange(n)
    day, block = quarter // 96, quarter // 16 - quarter[0] // 16
    accepted = np.where(np.isin(block, rejected), 0.5,
                        np.where(np.isin(block, tied), 1.0, 2.0))
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
    executed, specs, solved = [], [], {}
    spec, solve = afrr_rungs._Book.spec, afrr_rungs.solve_window_reserve

    def recorded(*args):
        # dispatch_step's arguments in order: wind, pv, soc, p_bat, curtail_frac, dt, cfg,
        # export_cap, reserve
        executed.append({**dispatch_step(*args), "proposed": args[3], "curtail_frac": args[4],
                         "reserve": args[8]})
        return executed[-1]

    def recorded_spec(book, start, stop, decide=None):
        specs.append((start, stop, decide, spec(book, start, stop, decide)))
        return specs[-1][-1]

    def recorded_plan(*args, **kwargs):
        # by the spec it was solved for, which `specs` keeps alive, so no id is reused
        solved[id(args[6])] = solve(*args, **kwargs)
        return solved[id(args[6])]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(afrr_rungs, "dispatch_step", recorded)
        patch.setattr(afrr_rungs._Book, "spec", recorded_spec)
        patch.setattr(afrr_rungs, "solve_window_reserve", recorded_plan)
        res = b3_rolling(wind, pv, eff, *forecasts, run, resolve_every=4, premium=premium,
                         afrr=prices)
    return {"wind": wind, "pv": pv, "eff": eff, "forecasts": forecasts, "premium": premium,
            "prices": prices, "run": run, "res": res, "executed": executed, "specs": specs,
            "plans": {start: solved[id(spec)] for start, _, decide, spec in specs
                      if decide is None}}


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
    and so are the bids it made before, for day 1; from the award on, a rejected product
    holds nothing, as B3 executes its steps and in its result, while the accepted ones hold
    what was bid. Day 2's bids come from a gate that starts from what each run did after the
    award, so they may differ."""
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
        assert np.array_equal(other[f"afrr_bid_{direction}"][day1], bid[day1]), direction
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
    reservations, reproduces the dispatch it executed, step for step. It re-runs the
    proposals, as the safety layer need not give back a battery power it applied when that
    power is proposed again (`afrr_rungs.b3_reserve`). Negative control: a re-simulation
    without the reservations, the archived project's bug, does not reproduce it."""
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


def _gate_window(days: int, wind, price, bid_up, bid_down, plant: PlantConfig = PLANT,
                 **market):
    """A window that starts at a gate, 09:00 on day 0, and runs to the end of day `days`:
    wind on `plant`, 60 MW of connection by default, and no PV, at `price` per step (each one
    for all, or per step), forecast as they are. B3 re-plans only where it must, at the gates
    and the awards, so the window is quick to run; it is for tests of what B3 bids. Returns
    (the arguments of b3_rolling up to the run's settings, the window's prices)."""
    n = 60 + 96 * days
    eff, wind = (np.broadcast_to(np.asarray(x, dtype=float), (n,)).copy() for x in (price, wind))
    prices = _prices(n, bid_up, bid_down, start_h=9)
    gen = (wind, np.zeros(n))
    return (*gen, eff, *gen, eff, _run(**market).with_(plant=plant)), prices


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


def test_b3_counts_the_re_plans_that_hold_reserve_and_fail(monkeypatch):
    """A re-plan that holds reserve has slack on every fixed reservation, so it is always
    feasible, and one not solved to optimality is the solver's failure: B3 then idles until
    its next plan, as today's B3 does, while it holds reserve, and the result counts it, so
    that it is reported rather than hidden. A re-plan that holds nothing is today's plan,
    whose failures today's B3 does not count. Here every re-plan fails; of the four, those
    at 09:00 and 09:30 on day 1 hold day 1's reservations, and only they count."""
    solve, failed = afrr_rungs.solve_window_reserve, []

    def failing_replans(*args, **kwargs):
        if args[6].integer:
            return solve(*args, **kwargs)
        failed.append(args[6])
        return None

    monkeypatch.setattr(afrr_rungs, "solve_window_reserve", failing_replans)
    monkeypatch.setattr(baselines, "solve_window", lambda *args, **kwargs: None)
    args, prices = _gate_window(2, 30.0, 50.0, 20.0, 5.0)
    res = b3_rolling(*args, resolve_every=10 ** 6, afrr=prices)

    assert len(failed) == 2 and res["afrr_replan_failures"] == 2
    assert res["afrr_commit_failures"] == 0 and np.any(res["afrr_up"][prices.day == 1])

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
    the 00-04 bid, which is rejected, and the re-plan at 09:30 holds 0 there; the 04-08 bid
    equals its marginal, and a tie counts as accepted (`reserve.paid_prices`), so it is held.
    B3 re-plans at each gate and award, whatever its resolve_every: here only there."""
    plans = _gate_plans(monkeypatch)
    args, prices = _gate_window(2, 30.0, 50.0, 20.0, 5.0)
    # day 1: 00-04 rejected, 04-08 tied
    prices = _prices(len(prices.block), 20.0, 5.0, start_h=9, rejected=(4,), tied=(5,))
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
    for direction in ("up", "down"):
        bid = res[f"afrr_bid_{direction}"][first + 16]
        held = (getattr(award, f"fixed_{direction}")[5], res[f"afrr_{direction}"][first + 16])
        assert bid >= 1.0 and held == (bid, bid), direction


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


def test_the_gate_bids_on_the_energy_stored_at_nine(monkeypatch):
    """A gate knows the energy stored at 09:00, not the window's start: the battery starts
    full at midnight and sells it all at 300 EUR/MWh before 02:00, and with no wind to charge
    from, the gate bids no positive reserve for day 1, which pays 30 EUR/MW/h. Negative
    control: a gate planned from the window's 38 MWh bids some, which the empty battery
    cannot hold."""
    n, zero = 192, np.zeros(192)
    price = np.where(np.arange(n) < 8, 300.0, 50.0)
    run = _run().with_(plant=PlantConfig(soc_init_frac=0.95))
    prices = _prices(n, 30.0, 0.0)

    def run_b3():
        return b3_rolling(zero, zero, price, zero, zero, price, run, resolve_every=10 ** 6,
                          afrr=prices)

    res = run_b3()
    assert res["soc"][GATE - 1] == pytest.approx(PLANT.soc_min, abs=1e-6)
    assert not np.any(res["afrr_bid_up"])
    bid = afrr_rungs._Book.bid
    monkeypatch.setattr(afrr_rungs._Book, "bid",
                        lambda book, t, soc, *args: bid(book, t, run.plant.soc_init, *args))
    assert run_b3()["afrr_bid_up"].max() >= 1.0


@pytest.fixture(scope="module")
def evening_then_morning():
    """B3 on two days from a gate, on 30 MW of wind and energy at 50 EUR/MWh, forecast as
    they are, where two products pay: 20 MW down in 20-24 on day 1 (product 9) and up in
    00-04 on day 2 (product 10). Its plans see 4 hours ahead, as in the other tests here, or
    24, the default; it re-plans only at the gates and awards. Returns ({horizon in steps:
    B3's result}, the window's prices)."""
    bid_up, bid_down = np.zeros(16), np.zeros(16)
    bid_down[9], bid_up[10] = 20.0, 30.0
    args, prices = _gate_window(2, 30.0, 50.0, bid_up, bid_down)
    return {steps: b3_rolling(*args[:-1], args[-1].with_(horizon_steps=steps),
                              resolve_every=10 ** 6, afrr=prices)
            for steps in (16, 96)}, prices


def test_the_gate_holds_what_b3_holds_on_the_gate_day(evening_then_morning):
    """A gate holds the rest of its own day as B3 holds it there. 20 MW down in 20-24 on day 1
    keep the battery at most at 19 MWh and forbid it to charge then (R2, R6), so at midnight
    it holds at most 19 MWh, and the gate at 09:00 on day 1 bids 4 MW up in 00-04 on day 2,
    what 17 MWh above the minimum deliver for 4 hours, not the 8 MW the battery could hold
    alone."""
    runs, prices = evening_then_morning
    for res in runs.values():
        assert _per_product(res["afrr_bid_down"], prices)[9] == 20.0
        assert _per_product(res["afrr_bid_up"], prices)[10] == 4.0


def test_b3_holds_an_award_only_where_its_plans_see_it_in_time(evening_then_morning):
    """What B3 holds rests on how far its plans see. The 4 MW up of 00-04 on day 2 need their
    energy stored before 20:00, when the 20 MW down forbid charging. Plans that see 24
    hours, the default, store it, and B3 holds all it was awarded. Plans that see 4 hours, as
    the other tests here use, see 00-04 only once charging is forbidden, and B3 misses the
    4 MW: a limit of the horizon, so runs on such a horizon do not show that B3 holds its
    awards."""
    runs, prices = evening_then_morning
    morning = prices.block == 10
    assert runs[96]["afrr_up"][morning].min() == 4.0
    assert not np.any(runs[96]["afrr_deficit_up"]) and not np.any(runs[96]["afrr_deficit_down"])
    assert runs[16]["afrr_deficit_up"][morning].max() > 3.0


def _fixture_prices(first: datetime.date, days: int) -> CapacityPrices:
    """Capacity prices of `days` Berlin days from `first`, on the window's UTC quarter-hours,
    read from an offline results workbook with invented prices (tests/afrr_fixtures.py)."""
    path = Path(__file__).with_name("afrr_fixtures.py")
    spec = importlib.util.spec_from_file_location("afrr_fixtures", path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    start, end = (pd.Timestamp(day.isoformat(), tz="Europe/Berlin")
                  for day in (first, first + datetime.timedelta(days=days)))
    index = pd.date_range(start, end, freq="15min", inclusive="left").tz_convert("UTC")
    workbook = fixtures.results_xlsx([first + datetime.timedelta(days=k) for k in range(days)])
    return capacity_prices(index, parse_results(workbook))


def test_b3_counts_each_product_for_its_own_hours_across_the_clock_changes(monkeypatch):
    """B3 is paid and charged for each product's own hours, so on the clock-change days of
    2025 the 00-04 product counts 3 hours in March and 5 in October, in the gate that bids it
    and in the re-plans that hold it, and B3 holds it for its 12 or 20 quarter-hours. The gate
    before the 23-hour day decides its 92 quarter-hours, the one before the 25-hour day 100.
    Invented prices from the offline workbook, 30 MW of wind and energy at 50 EUR/MWh."""
    specs, spec = [], afrr_rungs._Book.spec

    def recorded(book, start, stop, decide=None):
        specs.append((start, decide, spec(book, start, stop, decide)))
        return specs[-1][-1]

    monkeypatch.setattr(afrr_rungs._Book, "spec", recorded)
    for first, hours, steps in ((datetime.date(2025, 3, 29), 3.0, 92),
                                (datetime.date(2025, 10, 25), 5.0, 100)):
        specs.clear()
        prices = _fixture_prices(first, 2)
        n = len(prices.block)
        wind, price = np.full(n, 30.0), np.full(n, 50.0)
        res = b3_rolling(wind, np.zeros(n), price, wind, np.zeros(n), price,
                         _run().with_(horizon_steps=96), resolve_every=10 ** 6, afrr=prices)
        midnight = int(np.flatnonzero(prices.day == 1)[0])
        product = prices.block == prices.block[midnight]
        seen = [(start, decide, s) for start, decide, s in specs
                if start <= midnight < start + len(s.block)]
        assert np.count_nonzero(product) == 4 * hours
        assert [decide.sum() for _, decide, _ in seen if decide is not None] == [steps]
        assert len(seen) >= 2 and all(s.hours[s.block[midnight - start]] == hours
                                      for start, _, s in seen), first
        bid = res["afrr_bid_down"][midnight]
        assert bid >= 1.0 and np.all(res["afrr_down"][product] == bid), first

# ------------------------------------------------------------------ B3's rules (C8)
def _gate_bids(args, prices, **run) -> list[np.ndarray]:
    """What B3 bids, per product of day 1, up and down, at the gate of a `_gate_window`, with
    the run's settings changed by `run`."""
    res = b3_rolling(*args[:-1], args[-1].with_(**run), resolve_every=10 ** 6, afrr=prices)
    day1 = _per_product(prices.day == 1, prices)
    return [_per_product(res[f"afrr_bid_{d}"], prices)[day1] for d in ("up", "down")]


def test_b3_bids_no_upward_reserve_when_the_connection_always_binds():
    """Generation always 1.2 x a 20 MW connection, energy at 100 EUR/MWh and capacity at
    20 EUR/MW/h: positive reserve keeps its MW free below the connection, which costs exported
    energy worth more than it earns, so B3 bids none in all six products, as B2 holds none,
    and negative reserve rides on the export. Its derating leaves the connection and the
    objective on the central forecast. Negative control: derating all generation by 0.3, as
    the first draft did, makes the connection look loose, and the gate bids 3 MW up."""
    args, prices = _gate_window(1, 24.0, 100.0, 20.0, 20.0, plant=PlantConfig(conn_mw=20.0))
    up, down = _gate_bids(args, prices)
    assert not np.any(up) and np.all(down >= 1.0)

    wind, pv, eff, wind_fc, pv_fc, price_fc, run = args
    derated = (wind, pv, eff, 0.7 * wind_fc, pv_fc, price_fc, run)
    up, _ = _gate_bids(derated, prices, afrr_forecast_margin_sigmas=0.0)
    assert up.tolist() == [3.0] * 5 + [0.0]


def test_the_gate_counts_on_derated_generation_for_what_its_reservations_need():
    """Where a plan holds reserve, the export that covers negative reserve and the charging
    that fills positive reserve's band hold on generation derated by twice B3's forecast
    error, 0.3 at its 0.15. With 10 MW of wind from midnight and an empty battery, the
    export covers 7 MW of negative reserve, not 10. With 2 MW of wind charging the battery
    from the gate on, B3 stores 1.4 MW of it, enough for 4 MW up from midnight, not 6. With
    perfect forecasts, sigma 0, it counts on all of the wind."""
    night = np.r_[np.zeros(60), np.full(96, 10.0)]
    args, prices = _gate_window(1, night, 50.0, 0.0, 20.0)
    assert _gate_bids(args, prices)[1].tolist() == [7.0] * 6
    assert _gate_bids(args, prices, forecast_sigma=0.0)[1].tolist() == [10.0] * 6

    args, prices = _gate_window(1, 2.0, 50.0, 30.0, 0.0)
    derated, central = _gate_bids(args, prices)[0], _gate_bids(args, prices, forecast_sigma=0.0)[0]
    assert (derated[0], central[0]) == (4.0, 6.0)
    assert np.all(derated <= central)


def _spec(block, fixed_down, paid_down, integer=False, **kwargs) -> ReserveSpec:
    """A ReserveSpec on the products of `block` that holds negative reserve only: fixed_down
    and paid_down per product, or one for all; nothing up; B3's rule."""
    count = int(np.max(block)) + 1
    down = (np.broadcast_to(np.asarray(x, dtype=float), (count,)) for x in (fixed_down, paid_down))
    zero = np.zeros(count)
    return ReserveSpec(zero, next(down), zero, next(down), np.bincount(block) * DT,
                       np.asarray(block), RULE, 10_000.0, integer, 0.0, **kwargs)


def test_the_derating_applies_only_while_a_reservation_is_in_the_plan(monkeypatch):
    """The derating (R8) applies only to a plan that holds reserve. A spec that holds nothing
    adds no term to the LP. One that holds negative reserve bounds the charging on every step
    from derate_from and the export on the steps of its negative reservation, at 0.7 of the
    generation. B3 derates by twice its forecast error, 0.3 at today's 0.15: from a gate's
    first step, from a re-plan's second, whose first is the truth; with perfect forecasts,
    sigma 0, it derates nothing."""
    n, block = 32, np.arange(32) // 16
    wind, pv, price = _window(n, seed=1)

    def model(spec):
        m, v = baselines._energy_lp(wind, pv, price - 60.0, DT, _run(), 12.0)
        add_reserve_terms(m, v, spec, PLANT, 12.0, np.full(n, 60.0), gen=wind + pv)
        return m.constraints

    assert not [name for name in model(_spec(block, 0.0, 10.0, derate=0.3)) if "R8" in name]
    held = model(_spec(block, [3.0, 0.0], 10.0, derate=0.3, derate_from=1))
    assert sorted(name for name in held if name.startswith("R8_charge")) == sorted(
        f"R8_charge_{t}" for t in range(1, n))
    assert sorted(name for name in held if name.startswith("R8_export")) == sorted(
        f"R8_export_{t}" for t in range(1, 16))
    assert held["R8_charge_5"].constant == pytest.approx(-0.7 * (wind[5] + pv[5]), rel=1e-12)

    plans = _gate_plans(monkeypatch)
    args, prices = _gate_window(1, 30.0, 50.0, 20.0, 5.0)
    # on a 24-hour horizon the gate's own re-plan sees the next day's reservations
    day_ahead = args[-1].with_(horizon_steps=96)
    b3_rolling(*args[:-1], day_ahead, resolve_every=10 ** 6, afrr=prices)
    gate, replan = (next(spec for spec, _ in plans if spec.integer is kind)
                    for kind in (True, False))
    assert (gate.derate, gate.derate_from) == (pytest.approx(0.3), 0)
    assert (replan.derate, replan.derate_from) == (pytest.approx(0.3), 1)
    plans.clear()
    b3_rolling(*args[:-1], day_ahead.with_(forecast_sigma=0.0), resolve_every=10 ** 6,
               afrr=prices)
    assert len(plans) == 3 and all(spec.derate == 0.0 for spec, _ in plans)

def test_r8_prices_the_export_a_derated_forecast_lacks():
    """R8 asks the export of a fixed negative reservation to hold on derated generation, less
    what the plan may fall short of it, so a plan whose derated export cannot cover the
    reservation prices the shortfall and stays solvable. 10 MW down for 4 hours on 10 MW of
    wind, from an empty battery, derated by 0.3 from the plan's first step, as a gate derates:
    the export covers 7 MW, so the plan counts 3 MW not held at the penalty plus the
    20 EUR/MW/h they are paid, 120,240 EUR below the plan that trusts the forecast."""
    block = np.zeros(16, dtype=int)
    wind, pv, price = np.full(16, 10.0), np.zeros(16), np.full(16, 50.0)

    def objective(derate):
        plan = afrr_rungs.solve_window_reserve(wind, pv, price, DT, _run(), PLANT.soc_min,
                                               _spec(block, 10.0, 20.0, derate=derate))
        return plan.objective

    assert objective(0.0) - objective(0.3) == pytest.approx((10_000.0 + 20.0) * 3.0 * 4.0,
                                                            rel=1e-6)


def test_b3s_derating_is_set_by_its_forecast_error():
    """B3 derates by afrr_forecast_margin_sigmas forecast errors, all of the generation at
    most, and refuses a number of errors it cannot read."""
    assert RunConfig().afrr_derate == pytest.approx(0.3)
    assert RunConfig(forecast_sigma=0.8).afrr_derate == 1.0
    assert RunConfig(afrr_forecast_margin_sigmas=0.0).afrr_derate == 0.0
    for sigmas in (-0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="afrr_forecast_margin_sigmas"):
            RunConfig(afrr_forecast_margin_sigmas=sigmas)


def test_b3_curtails_as_planned_before_a_negative_product(monkeypatch):
    """Spill at a positive price, then negative reserve: from midnight 30 MW of wind meet a
    20 MW connection, and from 04:00 the battery holds 20 MW down, which needs 19 MWh of room
    below its maximum. B3's plans store some of the spill and curtail the rest, to keep that
    room. Executing what a fresh plan curtails, B3 holds the reservation. Negative control:
    with today's rule, which leaves the curtailment to the safety layer at a positive price,
    the layer stores the spill first, the battery fills, and the product falls short."""
    n = 92                                                  # 09:00 on day 0 to 08:00 on day 1
    wind = np.r_[np.full(60, 10.0), np.full(16, 30.0), np.full(16, 25.0)]
    eff = np.full(n, 50.0)
    prices = _prices(n, 0.0, [0.0] * 5 + [20.0], start_h=9)        # 04-08 on day 1
    run = _run().with_(plant=PlantConfig(conn_mw=20.0))

    def short_mwh():
        res = b3_rolling(wind, np.zeros(n), eff, wind, np.zeros(n), eff, run, afrr=prices)
        assert res["afrr_down"][76] == 20.0
        return res["afrr_deficit_down"].max() * 4.0, res["soc"][75]

    held = short_mwh()
    assert held[0] == 0.0 and held[1] <= PLANT.soc_max - 20.0 * PLANT.eta_c
    rule = afrr_rungs._executed_curtailment
    monkeypatch.setattr(afrr_rungs, "_executed_curtailment",
                        lambda planned, k, price, holds: rule(planned, k, price, False))
    assert short_mwh()[0] > 10.0


def test_b3_dips_below_its_positive_band_to_keep_its_negative_reserve():
    """B3 holds 4 MW up and 20 MW down from 04:00 to 20:00 on day 1, as much as both bands
    allow together, so its battery sits on the positive band, and its plans count on 30 MW of
    wind. At 18:00 the wind blows at 12 MW for one step. The plan made then, on the truth,
    discharges 8 MW so that export still covers the 20 MW down, and dips below the positive
    band for that quarter-hour, which leaves 0.5 MW of the 4 not held; the safety layer
    follows it. On 980b3b8 the layer refused every discharge below the band, and 7.9998 MW
    of the negative reservation were not held, charged for all of its product."""
    n = 60 + 96
    hour = (9 + np.arange(n) / 4) % 24
    price = np.where(hour >= 17, 120.0, 50.0)          # an evening peak keeps energy stored
    forecast = np.full(n, 30.0)
    truth = np.where(np.arange(n) == 132, 12.0, forecast)          # 18:00 on day 1
    res = b3_rolling(truth, np.zeros(n), price, forecast, np.zeros(n), price,
                     _run().with_(horizon_steps=96), resolve_every=12,
                     afrr=_prices(n, 25.0, 25.0, start_h=9))

    assert (res["afrr_up"][132], res["afrr_down"][132]) == (4.0, 20.0)
    assert res["export"][132] == pytest.approx(20.0, abs=1e-6)
    assert not np.any(res["afrr_deficit_down"])
    assert 0.0 < res["afrr_deficit_up"].max() <= 0.5 + 1e-9


def test_planned_curtailment_is_executed_only_from_a_fresh_plan(three_days):
    """In the step a plan holding reserve is made in, on the true generation, B3 curtails as
    the plan does, also at a non-negative price. In the steps that follow it, with
    resolve_every 4, the plan's curtailment was computed on forecasts, so today's rule
    applies: none at a non-negative price, the connection left to the safety layer; and so
    it does in plans that hold nothing."""
    case = three_days
    eff, prices, plans = case["eff"], case["prices"], case["plans"]
    executed = np.array([step["curtail_frac"] for step in case["executed"]])
    fresh = {t for t in range(288) if t % 4 == 0 or prices.gate[t] or prices.award[t]}

    assert all(executed[t] == plan.curt[0] for t, plan in plans.items())
    assert any(eff[t] >= 0.0 and plan.curt[0] > 0.0 for t, plan in plans.items())
    later = [t for t in range(288) if t not in fresh or t not in plans]
    assert not np.any(executed[later][eff[later] >= 0.0])
    followed = [(t, plans[s].curt[t - s]) for t in later if t not in fresh
                for s in [max(f for f in fresh if f <= t)] if s in plans]
    assert any(eff[t] >= 0.0 and curtail > 0.0 for t, curtail in followed)


def test_a_deficit_already_incurred_makes_later_dips_in_its_product_free(monkeypatch):
    """Once a deficit is measured in a product, the settlement charges it for all of the
    product whatever follows, so a re-plan spends nothing to recover it: its shortfall there
    is bounded below by the deficit. 10 MW down are held at night on 7 MW of wind, with 20 MWh
    in store and energy worth 200 EUR/MWh in the next product. With 3 MW already sunk, the
    plan exports the wind alone and keeps the energy for later; with none sunk, it would
    discharge 3 MW to cover the reservation. And B3 hands its re-plans the largest deficit
    measured so far in each product, not the last: a gate bids on 20 MW of wind forecast for
    the night that blows at 5, and at 18 in the step before each re-plan."""
    block = np.arange(32) // 16
    wind, pv = np.full(32, 7.0), np.zeros(32)
    price = np.r_[np.full(16, 50.0), np.full(16, 200.0)]
    run = _run()

    def plan(sunk):
        spec = _spec(block, [10.0, 0.0], [20.0, 0.0], sunk_down=[sunk, 0.0])
        return afrr_rungs.solve_window_reserve(wind, pv, price, DT, run, 20.0, spec)

    assert np.allclose(plan(3.0).p_bat[:16], 0.0, atol=1e-9)
    assert np.allclose(plan(0.0).p_bat[:16], 3.0, atol=1e-9)
    m, v = baselines._energy_lp(wind, pv, price, DT, run, 20.0)
    add_reserve_terms(m, v, _spec(block, [10.0, 0.0], [20.0, 0.0], sunk_down=[3.0, 0.0]),
                      PLANT, 20.0, np.full(32, 60.0))
    assert m.variablesDict()["u_down_0"].lowBound == 3.0

    specs, spec = [], afrr_rungs._Book.spec

    def recorded(book, start, stop, decide=None):
        specs.append((start, decide, spec(book, start, stop, decide)))
        return specs[-1][-1]

    monkeypatch.setattr(afrr_rungs._Book, "spec", recorded)
    n = 92                                                  # 09:00 on day 0 to 08:00 on day 1
    truth = np.r_[np.full(60, 10.0), np.tile([5.0, 5.0, 5.0, 18.0], 4), np.full(16, 20.0)]
    forecast = np.r_[np.full(60, 10.0), np.full(32, 20.0)]
    eff = np.full(n, 50.0)
    prices = _prices(n, 0.0, [0.0] * 4 + [20.0, 0.0], start_h=9)     # 00-04 on day 1
    res = b3_rolling(truth, np.zeros(n), eff, forecast, np.zeros(n), eff, run, resolve_every=4,
                     afrr=prices)
    deficit = res["afrr_deficit_down"]
    replans = [(start, s) for start, decide, s in specs if decide is None and 60 < start < 76]
    assert len(replans) == 3 and deficit[60:76].max() > 1.0
    for start, s in replans:
        assert deficit[start - 1] < deficit[60:start].max(), start
        assert s.sunk_down[0] == deficit[60:start].max() and s.sunk_up[0] == 0.0, start

def test_b3_sinks_a_positive_deficit_as_it_sinks_a_negative_one(monkeypatch):
    """B3 hands its re-plans the largest deficit measured so far in each product in both
    directions. The gate bids positive reserve for 00-04 on day 1 on 20 MW of wind forecast
    for the evening, to store energy worth 150 EUR/MWh from 04:00; the wind never blows, the
    battery meets the product empty, and every later re-plan in it bounds the product's
    positive shortfall below by what was measured."""
    specs, spec = [], afrr_rungs._Book.spec

    def recorded(book, start, stop, decide=None):
        specs.append((start, decide, spec(book, start, stop, decide)))
        return specs[-1][-1]

    monkeypatch.setattr(afrr_rungs._Book, "spec", recorded)
    n = 92                                                  # 09:00 on day 0 to 08:00 on day 1
    calm, forecast = np.zeros(n), np.r_[np.full(60, 20.0), np.zeros(32)]
    price = np.where(np.arange(n) < 76, 50.0, 150.0)
    prices = _prices(n, [0.0] * 4 + [30.0, 0.0], 0.0, start_h=9)     # 00-04 on day 1
    res = b3_rolling(calm, np.zeros(n), price, forecast, np.zeros(n), price, _run(),
                     resolve_every=4, afrr=prices)
    deficit = res["afrr_deficit_up"]
    replans = [(start, s) for start, decide, s in specs if decide is None and 60 < start < 76]
    assert len(replans) == 3 and deficit[60:76].max() >= 1.0
    for start, s in replans:
        assert s.sunk_up[0] == deficit[60:start].max() and s.sunk_down[0] == 0.0, start


def test_a_reserve_spec_refuses_a_sunk_deficit_or_derating_it_cannot_read():
    """A sunk deficit lies between 0 and its fixed reservation and is 0 where the plan
    decides; the derating lies between 0 and 1 and starts at a step of at least 0; and a
    spec that derates needs the plan's generation. Anything else is refused."""
    block = np.repeat([0, 1], 4)
    for kwargs in ({"sunk_down": [11.0, 0.0]}, {"sunk_down": [-1.0, 0.0]},
                   {"sunk_down": [np.nan, 0.0]}, {"sunk_down": [0.0, 0.0, 0.0]},
                   {"derate": -0.1}, {"derate": 1.5}, {"derate": np.nan},
                   {"derate_from": -1}, {"derate_from": 1.0}, {"derate_from": True}):
        with pytest.raises(ValueError, match="ReserveSpec"):
            _spec(block, [10.0, 0.0], 20.0, **kwargs)
    with pytest.raises(ValueError, match="ReserveSpec: a sunk deficit"):
        _spec(block, [np.nan, 0.0], 20.0, integer=True, sunk_down=[1.0, 0.0])
    for gen in (None, np.zeros(5)):
        m, v = baselines._energy_lp(np.zeros(8), np.zeros(8), np.zeros(8), DT, _run(), 20.0)
        with pytest.raises(ValueError, match="needs the plan's generation"):
            add_reserve_terms(m, v, _spec(block, [10.0, 0.0], 20.0, derate=0.3), PLANT, 20.0,
                              np.full(8, 60.0), gen=gen)

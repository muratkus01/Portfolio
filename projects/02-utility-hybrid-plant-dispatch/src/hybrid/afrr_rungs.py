"""The rungs of the ladder that reserve aFRR capacity: their window LP, B2, B3, and what a
ladder with aFRR refuses.

`baselines` hands over to this module only where a run reserves aFRR, so that with aFRR off
the ladder runs exactly the code it ran before. The model's terms are `reserve`'s; this
module builds them into today's window LP (`baselines._energy_lp`), solves it and reads the
plan.
"""
from __future__ import annotations

import time

import numpy as np
import pulp

from . import baselines
from .baselines import _energy_lp, _export_limit, _read_plan, default_terminal_price
from .config import RunConfig
from .plant import Reservation, dispatch_step, simulate
from .reserve import (PLAN_MARGIN, CapacityPrices, ReservePlan, ReserveRule, ReserveSpec,
                      add_reserve_terms, paid_prices, rounding_bound)

# B3's gate plans to the end of the day after the day it bids for, which holds no reserve,
# as its re-plans treat products not yet bid. A plan that ended with the day it bids for
# gave the energy stored at midnight no value, so holding positive reserve through 20-24
# looked like losing that energy.
_GATE_DAYS = 2


def solve_window_reserve(wind: np.ndarray, pv: np.ndarray, eff_price: np.ndarray, dt: float,
                         run: RunConfig, soc0: float, spec: ReserveSpec,
                         terminal_price: float = 0.0,
                         export_cap: np.ndarray | None = None) -> ReservePlan | None:
    """`baselines.solve_window` with the aFRR reservations of `spec`: today's energy LP, the
    reserve terms of `reserve.add_reserve_terms`, solved to proven optimality.

    wind, pv, eff_price, dt, run, soc0, terminal_price, export_cap: as for `solve_window`.
    spec: the plan's reservations, one product per step.
    Returns the plan (`reserve.ReservePlan`), or None where the solve is not proven optimal.
    """
    m, v = _energy_lp(wind, pv, eff_price, dt, run, soc0, terminal_price, export_cap)
    up, down = add_reserve_terms(m, v, spec, run.plant, soc0,
                                 _export_limit(len(wind), run.plant, export_cap),
                                 gen=np.asarray(wind, float) + np.asarray(pv, float))
    m.solve(pulp.PULP_CBC_CMD(msg=False))
    # A time limit stops CBC early and PuLP still sets status to Optimal; only sol_status
    # tells a proven optimum from the best plan found so far.
    if m.status != pulp.LpStatusOptimal or m.sol_status != pulp.LpSolutionOptimal:
        return None
    p_bat, curt = _read_plan(v, wind, pv)
    soc = np.array([float(v["soc"][t].value()) for t in range(len(wind))])
    return ReservePlan(p_bat, curt, _planned(up, spec.fixed_up, spec, run.plant.bess_mw),
                       _planned(down, spec.fixed_down, spec, run.plant.bess_mw), soc,
                       float(pulp.value(m.objective)))


def _planned(terms: list, fixed: np.ndarray, spec: ReserveSpec, rating: float) -> np.ndarray:
    """A solved plan's reservations in one direction, per step of the plan.

    terms: per product, as `add_reserve_terms` returned them. fixed: spec's fixed_* of that
    direction, NaN where decided. rating: the battery's MW.
    A decision's value is clipped to [0, rating], where the solver leaves residues such as
    -1e-12 that the safety layer refuses, and rounded to whole MW where decided so; a fixed
    reservation is read as given.
    """
    values = np.array([pulp.value(term) or 0.0 for term in terms], dtype=float)
    decided = np.isnan(fixed)
    values = np.where(decided, np.clip(values, 0.0, rating), values)
    if spec.integer:
        values = np.where(decided, np.round(values), values)
    return values[spec.block]


# ------------------------------------------------------------------ B2
def b2_reserve(wind: np.ndarray, pv: np.ndarray, eff_price: np.ndarray, run: RunConfig,
               prices: CapacityPrices,
               export_cap: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """B2 with aFRR: one LP over the window, with perfect foresight of generation, prices and
    awards, that co-optimises continuous reservations with the energy schedule.

    It is paid on run.market.afrr_price_basis and decides every product that pays (one
    paying nothing is fixed at 0), relaxing the whole-MW rule and the floor; it plans its
    energy and export with reserve.PLAN_MARGIN. Its plan is replayed through the safety
    layer with its reservations, as `baselines.b2_perfect` replays today's. On "bid" it is a
    ceiling, up to that margin, for B3, which is paid the same bids and holds whole MW under
    the floor.
    wind, pv, eff_price, export_cap: as for `baselines.b2_perfect`. run: the run's settings.
    prices: the window's capacity prices (`afrr_data.capacity_prices`).
    Returns the dispatch arrays of `plant.simulate` with the reservations, and
    "afrr_rounding_bound_eur": an upper bound on what the two relaxations overstate
    (`rounding_bound`).
    Raises RuntimeError where the LP is not solved to optimality, and where the replay
    leaves reserve not held (`_refuse_unheld_replay`).
    """
    paid_up, paid_down = paid_prices(prices, run.market.afrr_price_basis)
    spec = ReserveSpec.decide_all(prices.block, paid_up, paid_down, run, integer=False,
                                  floor_mwh=0.0, margin=PLAN_MARGIN)
    soc0 = run.plant.soc_init
    plan = solve_window_reserve(wind, pv, eff_price, run.dt, run, soc0, spec,
                                export_cap=export_cap)
    if plan is None:
        raise RuntimeError("B2 infeasible")
    res = simulate(wind, pv, plan.p_bat, plan.curt, run.dt, run.plant, export_cap=export_cap,
                   reserve_up=plan.r_up, reserve_down=plan.r_down, rule=spec.rule)
    res["afrr_rounding_bound_eur"] = rounding_bound(
        plan, spec, soc0, run.market.afrr_floor_hours * run.plant.bess_mw, run.plant)
    _refuse_unheld_replay("B2 perfect foresight", res, prices.block)
    return res


def _refuse_unheld_replay(name: str, res: dict[str, np.ndarray], block: np.ndarray) -> None:
    """Stop where the replay of a plan that holds every reservation leaves some not held.

    B2's reservations are hard constraints of its LP, without slack, so its plan cannot fall
    short of them. A deficit in its replay (at or above plant.TOL, below which the safety
    layer counts none) is a solver residue larger than the margin covers, and settling it as
    reserve not held would hide that.
    name: the rung, for the message. res: its replay (`plant.simulate` with reservations).
    block: the product of each step.
    Raises RuntimeError naming the first such step, its product, the reservation and the
    residue.
    """
    short = [(int(np.flatnonzero(res[f"afrr_deficit_{direction}"] > 0)[0]), direction)
             for direction in ("up", "down") if np.any(res[f"afrr_deficit_{direction}"] > 0)]
    if not short:
        return
    t, direction = min(short)
    raise RuntimeError(
        f"{name}'s replay holds {res[f'afrr_deficit_{direction}'][t]:.3g} MW too little of its "
        f"{res[f'afrr_{direction}'][t]:.6g} MW {direction} reservation at step {t} (product "
        f"{block[t]}). Its plan holds every reservation as a hard constraint, so this is a "
        "solver residue larger than reserve.PLAN_MARGIN covers")


# ------------------------------------------------------------------ B3
def b3_reserve(wind: np.ndarray, pv: np.ndarray, eff_price: np.ndarray, wind_fc: np.ndarray,
               pv_fc: np.ndarray, price_fc: np.ndarray, run: RunConfig, prices: CapacityPrices,
               premium=None, export_cap: np.ndarray | None = None, resolve_every: int = 1,
               progress: bool = False) -> dict[str, np.ndarray]:
    """B3 with aFRR: at each 09:00 gate it bids the next day's capacity, at 09:30 it learns
    which bids were accepted, and every step it re-plans its energy around what it holds.

    At a gate B3 knows its state of charge, its forecasts from the gate on, the premium of
    the gate step and its bids' prices, the same products' German averages of the day before;
    it bids in whole MW what a plan to the end of the following day holds (`_Book.bid`). It
    holds its bids as if accepted until the award, then the accepted ones (`_Book.award`), and
    never revises them. A plan whose horizon holds no reserve is today's B3 plan, so before
    its first gate B3 runs as it does without aFRR, step for step. The window's first day has
    no gate inside the window, so B3 reserves nothing on it.
    Three rules keep what it was awarded through its forecast errors, where a plan holds
    reserve: the plans derate the generation a reservation rests on by run.afrr_derate (R8);
    the step a plan is made in, on the true generation, curtails as planned
    (`_executed_curtailment`); and a product's deficit, once measured, is sunk in the later
    plans (`_Book.record`), which spend nothing to recover it.
    wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, premium, export_cap, resolve_every,
    progress: as for `baselines.b3_rolling`. prices: the window's capacity prices
    (`afrr_data.capacity_prices`); B3 reads their realised marginal only at the award.
    Returns the dispatch arrays of `plant.simulate` with the reservations held, "solve_time_s"
    and "terminal_price_eur_mwh" per re-plan as `b3_rolling` returns them, and
    "afrr_bid_up" and "afrr_bid_down", the MW bid per step before the award;
    "afrr_commit_solve_time_s", the seconds of each gate's solve; "afrr_commit_failures", the
    gates whose plan was not solved to optimality, which bid nothing; and
    "afrr_replan_failures", the re-plans that held reserve and were not solved, after which
    B3 idled until its next plan.
    Raises ValueError where a day B3 bids for has no bid price.
    """
    cfg, n = run.plant, len(wind)
    book = _Book(prices, run)
    soc = cfg.soc_init
    p_proposed, curt_applied = np.zeros(n), np.zeros(n)
    solve_times: list[float] = []
    terminal_prices: list[float] = []
    plan, offset = None, 0
    for t in range(n):
        fresh = plan is None or t % resolve_every == 0
        if prices.gate[t]:
            book.bid(t, soc, (wind_fc, pv_fc, price_fc), premium)
            fresh = True
        if prices.award[t]:
            book.award(t)
            fresh = True
        if fresh:
            plan = _replan(t, soc, (wind, pv, eff_price), (wind_fc, pv_fc, price_fc), premium,
                           book, export_cap, solve_times, terminal_prices)
            offset = t
        k = t - offset
        pb = float(plan[0][k]) if k < len(plan[0]) else 0.0
        cf = _executed_curtailment(plan[1], k, float(eff_price[t]), plan[2])
        r = dispatch_step(float(wind[t]), float(pv[t]), soc, pb, cf, run.dt, cfg,
                          None if export_cap is None else float(export_cap[t]),
                          Reservation(float(book.up[t]), float(book.down[t]), book.rule))
        book.record(t, r)
        p_proposed[t], curt_applied[t] = pb, cf
        soc = r["soc"]
        if progress and t % max(1, n // 10) == 0:
            print(f"    B3 {100 * t / n:3.0f}%", flush=True)
    # The result is what B3 executed: the same proposals with the same reservations, step by
    # step. Without the reservations, a replay would use the headroom they keep. And the
    # battery power applied, proposed again, is not always applied as it is: where the
    # battery discharges into an export the connection must curtail, the safety layer takes
    # its charging room off the discharge once more (`plant.dispatch_step`).
    res = simulate(wind, pv, p_proposed, curt_applied, run.dt, cfg, export_cap=export_cap,
                   reserve_up=book.up, reserve_down=book.down, rule=book.rule)
    res.update(solve_time_s=np.array(solve_times),
               terminal_price_eur_mwh=np.array(terminal_prices),
               afrr_bid_up=book.bid_up.copy(), afrr_bid_down=book.bid_down.copy(),
               afrr_commit_solve_time_s=np.array(book.gate_seconds),
               afrr_commit_failures=book.failures,
               afrr_replan_failures=book.replan_failures)
    return res


def _replan(t: int, soc: float, truth: tuple, forecasts: tuple, premium, book: _Book,
            export_cap: np.ndarray | None, solve_times: list[float],
            terminal_prices: list[float]) -> tuple[np.ndarray, np.ndarray, bool]:
    """B3's plan made in step t, as today's B3 makes it (`baselines.b3_rolling`): over its
    horizon, on the truth in step t and the forecasts after it, the premium priced at step t's
    rate. Where B3 holds or has bid reserve in the horizon, the plan holds it fixed, and
    each MWh it falls short costs the penalty plus the bid (`_Book.spec`).
    truth: (wind, pv, eff_price) per step of the window; forecasts: their forecasts.
    Appends the solve's seconds and the plan's terminal price to the two lists.
    Returns (battery power, curtailment fraction) per step of the horizon, zeros where the
    plan is not solved, as today's B3 idles then; and whether the plan holds reserve. A plan
    that holds reserve and is not solved is counted in book.replan_failures.
    """
    run = book.run
    stop = min(t + run.horizon_steps, len(truth[0]))
    w, s, pr = (np.concatenate([[now[t]], ahead[t + 1:stop]])
                for now, ahead in zip(truth, forecasts))
    pr = _planning_price(pr, premium, t, stop)
    ec = None if export_cap is None else export_cap[t:stop]
    terminal_prices.append(default_terminal_price(pr, run))
    started = time.perf_counter()
    holds = book.reserves(t, stop)
    if holds:
        plan = solve_window_reserve(w, s, pr, run.dt, run, soc, book.spec(t, stop),
                                    terminal_price=terminal_prices[-1], export_cap=ec)
        sol = None if plan is None else (plan.p_bat, plan.curt)
        if plan is None:
            # every fixed reservation has its slack, so such a plan is always feasible, and
            # one not solved is the solver's failure; B3 idles while it holds reserve, which
            # is reported rather than hidden
            book.replan_failures += 1
    else:
        sol = baselines.solve_window(w, s, pr, run.dt, run, soc,
                                     terminal_price=terminal_prices[-1], export_cap=ec)
    solve_times.append(time.perf_counter() - started)
    return (*(sol if sol is not None else (np.zeros(stop - t), np.zeros(stop - t))), holds)


def _executed_curtailment(planned: np.ndarray, k: int, price: float, holds: bool) -> float:
    """The share of generation B3 curtails in the k-th step of a plan, as planned, but at a
    non-negative price: today's rule (`baselines.b3_rolling`) curtails none there and leaves
    the connection to the safety layer, which stores spill in the battery first, as the
    plan's curtailment was computed on forecasts. Before a negative reservation that storing
    can fill the battery above the room the product will need, so in the step a plan holding
    reserve was made in (k == 0), whose generation is the truth, B3 curtails as planned.
    planned: the plan's curtailment fractions. price: the step's effective price, EUR/MWh.
    holds: whether the plan holds reserve.
    """
    curtail = float(planned[k]) if k < len(planned) else 0.0
    if price >= 0.0 and not (k == 0 and holds):
        return 0.0
    return curtail


def _planning_price(price: np.ndarray, premium, start: int, stop: int) -> np.ndarray:
    """The prices a plan made in step `start` sees for steps start to stop - 1: `price`, with
    the premium priced at the rate of step `start` wherever it is payable, as
    `baselines.b3_rolling` prices a horizon.
    premium: None, or (rate, eligible) per step of the window."""
    if premium is None:
        return price
    rate, eligible = premium
    return price + np.where(eligible[start:stop], rate[start] - rate[start:stop], 0.0)


def _gate_stop(day: np.ndarray, t: int) -> int:
    """Where the plan of the gate in step t ends: after the day _GATE_DAYS after the gate's,
    or at the window's end. day: the Berlin day of each step."""
    after = np.flatnonzero(day > day[t] + _GATE_DAYS)
    return int(after[0]) if len(after) else len(day)


class _Book:
    """B3's aFRR commitments over a window, per step: what it bid at each gate, and what it
    holds, which is its bid until the award and the accepted bid after it; and per product
    the largest deficit measured so far, its sunk deficit.

    prices: the window's capacity prices. run: the run's settings; its market sets the energy
    rule, with the floor, as B3 bids whole MW, and the penalty; run.afrr_derate sets R8.
    """

    def __init__(self, prices: CapacityPrices, run: RunConfig) -> None:
        market, n = run.market, len(prices.block)
        self.prices, self.run = prices, run
        self.product = np.unique(prices.block, return_inverse=True)[1].ravel()
        # a product is paid and charged for all its hours, also where a plan sees only some
        self.hours = np.bincount(self.product) * run.dt
        self.rule = ReserveRule(market.afrr_energy_hours_up, market.afrr_energy_hours_down,
                                market.afrr_floor_hours * run.plant.bess_mw)
        self.up, self.down = np.zeros(n), np.zeros(n)
        self.bid_up, self.bid_down = np.zeros(n), np.zeros(n)
        self.sunk_up, self.sunk_down = np.zeros(len(self.hours)), np.zeros(len(self.hours))
        self.gate_seconds: list[float] = []
        self.failures, self.replan_failures = 0, 0

    def reserves(self, start: int, stop: int) -> bool:
        """Whether B3 holds or has bid reserve in any step from start to stop - 1."""
        return bool(np.any(self.up[start:stop] > 0) or np.any(self.down[start:stop] > 0))

    def record(self, t: int, step: dict) -> None:
        """Add what step t executed (`plant.dispatch_step`'s result) to its product's sunk
        deficit: the settlement charges a product's largest shortfall once, for all of it."""
        product = self.product[t]
        self.sunk_up[product] = max(self.sunk_up[product], step["afrr_deficit_up"])
        self.sunk_down[product] = max(self.sunk_down[product], step["afrr_deficit_down"])

    def spec(self, start: int, stop: int, decide: np.ndarray | None = None) -> ReserveSpec:
        """Steps start to stop - 1 as a plan's ReserveSpec: each product fixed at what B3 holds
        there and paid its bid, 0 where it has none, with its sunk deficit; or, where `decide`
        (one bool per step) is True and the bid pays, decided in whole MW with the floor, as a
        gate decides. R8 applies from the plan's first step at a gate, which plans on the
        forecasts alone, and from its second in a re-plan, whose first step is the truth."""
        steps = slice(start, stop)
        paid = [np.nan_to_num(bid[steps], nan=0.0)
                for bid in (self.prices.bid_up, self.prices.bid_down)]
        fixed = [held[steps] for held in (self.up, self.down)]
        if decide is not None:
            # a product whose bid pays nothing is not bid for
            fixed = [np.where(decide & (price > 0), np.nan, held)
                     for held, price in zip(fixed, paid)]
        labels, local = np.unique(self.product[steps], return_inverse=True)
        first = np.unique(local, return_index=True)[1]
        return ReserveSpec(fixed_up=fixed[0][first], fixed_down=fixed[1][first],
                           paid_up=paid[0][first], paid_down=paid[1][first],
                           hours=self.hours[labels], block=local.ravel(), rule=self.rule,
                           penalty=self.run.market.afrr_shortfall_penalty_eur_mwh,
                           integer=decide is not None, margin=0.0,
                           sunk_up=self.sunk_up[labels], sunk_down=self.sunk_down[labels],
                           derate=self.run.afrr_derate,
                           derate_from=0 if decide is not None else 1)

    def bid(self, t: int, soc: float, forecasts: tuple, premium) -> None:
        """B3's bids at the gate in step t, for the next day, held from then on as if accepted.

        The plan runs from the gate to `_gate_stop`, on the forecasts alone, from soc, the
        energy stored at the gate; it holds what B3 holds on the gate's day, decides the next
        day's products in whole MW, priced at their bids, and holds nothing after. Where it is
        not solved to optimality, B3 bids nothing and counts the gate as a failure.
        forecasts: (wind_fc, pv_fc, price_fc) per step of the window. premium: as for
        `b3_reserve`. Raises ValueError where the next day has no bid price.
        """
        prices, run = self.prices, self.run
        stop = _gate_stop(prices.day, t)
        day = prices.day[t] + 1
        decide = prices.day[t:stop] == day
        if not all(np.all(np.isfinite(bid[t:stop][decide]))
                   for bid in (prices.bid_up, prices.bid_down)):
            raise ValueError(f"B3 has no bid price for day {day} of the window, which it bids "
                             f"for at step {t}")
        wind_fc, pv_fc, price_fc = (forecast[t:stop] for forecast in forecasts)
        price = _planning_price(price_fc, premium, t, stop)
        started = time.perf_counter()
        plan = solve_window_reserve(wind_fc, pv_fc, price, run.dt, run, soc,
                                    self.spec(t, stop, decide),
                                    terminal_price=default_terminal_price(price, run))
        self.gate_seconds.append(time.perf_counter() - started)
        steps = np.flatnonzero(prices.day == day)
        if plan is None:
            self.failures += 1
        up, down = (0.0, 0.0) if plan is None else (plan.r_up[decide], plan.r_down[decide])
        self.up[steps] = self.bid_up[steps] = up
        self.down[steps] = self.bid_down[steps] = down

    def award(self, t: int) -> None:
        """At the award in step t: keep the next day's bids that were accepted, at or below
        the realised marginal as `reserve.paid_prices` accepts them, and drop the rest."""
        prices = self.prices
        day = prices.day == prices.day[t] + 1
        for held, bid, marginal in ((self.up, prices.bid_up, prices.marginal_up),
                                    (self.down, prices.bid_down, prices.marginal_down)):
            held[day] = np.where(bid[day] <= marginal[day], held[day], 0.0)


# ------------------------------------------------------------------ the ladder's switches
def refuse_what_afrr_cannot_run(prices: CapacityPrices | None, run: RunConfig, steps: int,
                                export_cap: np.ndarray | None, include_b3: bool) -> None:
    """Stop a ladder with aFRR that this model cannot run or settle as specified.

    prices: the window's capacity prices, or None. run: the run's settings; the switch is
    run.market.afrr_enabled. steps: the window's length. export_cap: the redispatch order,
    or None. include_b3: whether B3 runs.
    Raises ValueError for: prices with the switch off, or the switch on without prices;
    prices for another number of steps; an export cap or grid charging, which the model does
    not bound for negative reserve; a connection below the battery's rating, where the
    connection headroom of positive reserve cannot be kept; B3 on the "average" basis, which
    pays a bid nobody knows at the gate; and a penalty at or below a price paid in the window,
    as the penalty stands in for MfRRA's incentive component, charged on top of the cut, and
    is meant to lie above every price the run pays. That alone does not keep a plan from
    holding less than it sold: the cut takes back what each MW-hour not held was paid, so
    holding less never pays on the capacity side at any penalty, and what the penalty must
    outweigh is the energy a shortfall frees, as the default of 10,000 EUR/MWh does by far.
    """
    market, plant = run.market, run.plant
    if prices is None or not market.afrr_enabled:
        raise ValueError("aFRR needs both market.afrr_enabled and afrr=, the window's capacity "
                         f"prices; afrr_enabled is {market.afrr_enabled} and afrr= is "
                         f"{'missing' if prices is None else 'given'}")
    if len(prices.block) != steps:
        raise ValueError(f"afrr= holds {len(prices.block)} steps and the window {steps}")
    if export_cap is not None or plant.grid_charging_allowed:
        raise ValueError("aFRR refuses an export cap and grid charging: negative reserve must "
                         "be served from the plant's own export, and the model bounds neither "
                         "a redispatch order nor grid imports for it")
    if plant.conn_mw < plant.bess_mw:
        raise ValueError(f"aFRR needs a connection of at least the battery's {plant.bess_mw:g} "
                         f"MW, not {plant.conn_mw:g} MW, to keep positive reserve's headroom")
    if include_b3 and market.afrr_price_basis == "average":
        raise ValueError("B3 bids, so it cannot be paid the realised average, which nobody "
                         "knows at the gate; run B3 on \"bid\", or the \"average\" basis "
                         "without B3")
    # What B2 is paid; B0 and B1 are paid the same and hold nothing. B3, paid its accepted
    # bids, is paid no more on the bases it runs on: the same on "bid", and at most the
    # marginal on "marginal", as a bid is accepted at or below it.
    highest = max(float(np.max(p)) for p in paid_prices(prices, market.afrr_price_basis))
    if market.afrr_shortfall_penalty_eur_mwh <= highest:
        raise ValueError(f"afrr_shortfall_penalty_eur_mwh "
                         f"({market.afrr_shortfall_penalty_eur_mwh:,.2f}) must exceed every "
                         f"capacity price paid in the window ({highest:,.2f} EUR/MW/h)")

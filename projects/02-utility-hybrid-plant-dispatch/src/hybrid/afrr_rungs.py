"""The rungs of the ladder that reserve aFRR capacity: their window LP, B2, and what a
ladder with aFRR refuses.

`baselines` hands over to this module only where a run reserves aFRR, so that with aFRR off
the ladder runs exactly the code it ran before. The model's terms are `reserve`'s; this
module builds them into today's window LP (`baselines._energy_lp`), solves it and reads the
plan.
"""
from __future__ import annotations

import numpy as np
import pulp

from .baselines import _energy_lp, _export_limit, _read_plan
from .config import RunConfig
from .plant import simulate
from .reserve import (PLAN_MARGIN, CapacityPrices, ReservePlan, ReserveSpec, add_reserve_terms,
                      paid_prices, rounding_bound)


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
                                 _export_limit(len(wind), run.plant, export_cap))
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
    NotImplementedError for B3, which does not bid aFRR capacity yet.
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
    # what B2 is paid; B0 and B1 are paid the same and hold nothing
    highest = max(float(np.max(p)) for p in paid_prices(prices, market.afrr_price_basis))
    if market.afrr_shortfall_penalty_eur_mwh <= highest:
        raise ValueError(f"afrr_shortfall_penalty_eur_mwh "
                         f"({market.afrr_shortfall_penalty_eur_mwh:,.2f}) must exceed every "
                         f"capacity price paid in the window ({highest:,.2f} EUR/MW/h)")
    if include_b3:
        raise NotImplementedError("B3 does not bid aFRR capacity yet; run the ladder with "
                                  "afrr= and include_b3=False")

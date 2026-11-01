"""The rungs of the ladder that reserve aFRR capacity: their window LP, solved.

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
from .reserve import ReservePlan, ReserveSpec, add_reserve_terms


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

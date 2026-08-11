"""The benchmark ladder: B1 rule-based, B2 perfect foresight, B3 rolling-horizon MPC.

B0 (historical reality) requires metered site operation and is omitted where none exists;
that omission is stated rather than papered over with a synthetic stand-in.

The invariant that must hold on every window:

    net_cost(B2)  <=  net_cost(B3)  <=  net_cost(B1)

B2 has perfect foresight, so nothing can beat it; B3 is the deployable classical optimum and
should beat a price-blind heuristic. If this ordering is ever violated, there is a bug - it is
the cheapest and most effective invariant in the whole project, and `run_ladder` checks it.
"""
from __future__ import annotations

import time

import numpy as np

from ..config import RunConfig
from ..market import settlement
from ..model import site
from ..safety import project, project_series
from .milp import default_terminal_price, solve_window


# ----------------------------------------------------------------------------- B1
def b1_rule_based(load: np.ndarray, pv: np.ndarray, dt: float, run: RunConfig,
                  soc0: float | None = None,
                  dim_limit: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Self-consumption heuristic - the standard home energy management logic.

    Charge from PV surplus, discharge to cover deficit, never trade with the grid for price
    reasons. Price-blind by design: this is what an off-the-shelf hybrid inverter does, and it
    is the status quo the whole exercise is measured against.

    The safety layer does all the clipping, so B1 is genuinely three lines of intent.
    """
    surplus = pv - load                     # >0 -> PV surplus, <0 -> deficit
    proposed = -surplus                     # charge the surplus, discharge the deficit
    p_bat = project_series(proposed, load, pv, dt, run.site, soc0, dim_limit)
    return site.simulate(p_bat, load, pv, dt, run.site, soc0)


# ----------------------------------------------------------------------------- B2
def b2_perfect_foresight(load: np.ndarray, pv: np.ndarray, price_import: np.ndarray,
                         price_export: np.ndarray, dt: float, run: RunConfig,
                         soc0: float | None = None) -> dict[str, np.ndarray] | None:
    """One MILP over the entire evaluation period, with exact knowledge of the future.

    Not a controller: an upper bound on what the flexibility is worth. Reporting B2 as
    achievable performance was defect D5 of the original model.
    """
    s0 = run.site.soc_init if soc0 is None else soc0
    return solve_window(
        load, pv, price_import, price_export, dt, run.site, s0,
        terminal_price=0.0,                 # no terminal value: the period genuinely ends
        use_binaries=run.use_binaries,
        throughput_cap=run.site.e_throughput_max_per_day,
        steps_per_day=run.steps_per_day,
    )


# ----------------------------------------------------------------------------- B3
def b3_rolling_mpc(load: np.ndarray, pv: np.ndarray,
                   price_import: np.ndarray, price_export: np.ndarray,
                   load_fc: np.ndarray, pv_fc: np.ndarray,
                   dt: float, run: RunConfig, soc0: float | None = None,
                   dim_limit: np.ndarray | None = None,
                   resolve_every: int = 1,
                   progress: bool = False) -> dict[str, np.ndarray]:
    """Rolling-horizon MPC: the deployable classical optimum, and the bar RL must clear.

    At each decision step the controller solves the MILP over `run.horizon_steps` using
    FORECASTS of load and PV, applies only the first action to the true system, then advances
    and re-solves. Day-ahead prices are treated as known (they are published at ~13:00 D-1),
    which is a modelling choice, not an oversight - see the roadmap, Phase 5.5.

    `resolve_every > 1` re-optimises less often and holds the intervening planned actions.
    That weakens the baseline, so it defaults to 1; it exists only to make long sweeps
    affordable, and any run that uses it must report it.
    """
    n = len(load)
    s = run.site.soc_init if soc0 is None else soc0
    p_applied = np.zeros(n)
    solve_times: list[float] = []
    plan: np.ndarray | None = None
    plan_offset = 0

    for t in range(n):
        if t % resolve_every == 0 or plan is None:
            h = min(run.horizon_steps, n - t)
            # the current step is observed; the rest of the horizon is forecast
            lo = np.concatenate([[load[t]], load_fc[t + 1:t + h]])
            pvw = np.concatenate([[pv[t]], pv_fc[t + 1:t + h]])
            pi = price_import[t:t + h]
            pe = price_export[t:t + h]

            t0 = time.perf_counter()
            sol = solve_window(
                lo, pvw, pi, pe, dt, run.site, s,
                terminal_price=default_terminal_price(pi, run.site, run,
                                                      price_export=pe,
                                                      net_load=lo - pvw),
                use_binaries=run.use_binaries,
            )
            solve_times.append(time.perf_counter() - t0)
            plan = sol["p_bat"] if sol is not None else np.zeros(h)
            plan_offset = t

        proposed = float(plan[t - plan_offset]) if (t - plan_offset) < len(plan) else 0.0

        # apply the first planned action to the TRUE system, through the safety layer
        d = None if dim_limit is None else float(dim_limit[t])
        p = project(proposed, s, float(load[t]), float(pv[t]), dt, run.site, d)
        p_applied[t] = p
        s = site.soc_next(s, p, dt, run.site)

        if progress and t % max(1, n // 10) == 0:
            print(f"    B3 {100 * t / n:3.0f}%", flush=True)

    res = site.simulate(p_applied, load, pv, dt, run.site, soc0)
    res["solve_time_s"] = np.array(solve_times)
    return res


# ----------------------------------------------------------------------------- forecasts
def ar1_noise(n: int, rng: np.random.Generator, rho: float = 0.75) -> np.ndarray:
    """Autocorrelated standard-normal noise - forecast errors are not white."""
    z = rng.standard_normal(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * z[i]
    return z


def make_forecasts(load: np.ndarray, pv: np.ndarray, run: RunConfig,
                   seed: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Truth plus autocorrelated relative error.

    A deliberately simple stand-in for a real forecast model, kept because it is honest about
    what it is: B3 and the RL agent consume the SAME arrays, so neither has an information
    advantage. Phase 5 of the roadmap replaces this with an issue-time forecast store fed by
    DWD NWP and pvlib.
    """
    rng = np.random.default_rng(run.seed if seed is None else seed)
    n = len(load)
    load_fc = np.clip(load * (1 + run.forecast_sigma * ar1_noise(n, rng, run.forecast_rho)), 0, None)
    pv_fc = np.clip(pv * (1 + run.forecast_sigma * ar1_noise(n, rng, run.forecast_rho)), 0, None)
    return load_fc, pv_fc


# ----------------------------------------------------------------------------- driver
def run_ladder(load: np.ndarray, pv: np.ndarray, price_import: np.ndarray,
               price_export: np.ndarray, run: RunConfig,
               dim_limit: np.ndarray | None = None,
               include_b3: bool = True, progress: bool = False) -> dict[str, dict]:
    """Run B1, B2 and B3 on the same data and return their trajectories plus costs."""
    dt = run.dt
    out: dict[str, dict] = {}

    b1 = b1_rule_based(load, pv, dt, run, dim_limit=dim_limit)
    out["B1 rule-based"] = b1

    b2 = b2_perfect_foresight(load, pv, price_import, price_export, dt, run)
    if b2 is not None:
        out["B2 perfect foresight"] = b2

    if include_b3:
        load_fc, pv_fc = make_forecasts(load, pv, run)
        out["B3 rolling MPC"] = b3_rolling_mpc(
            load, pv, price_import, price_export, load_fc, pv_fc, dt, run,
            dim_limit=dim_limit, progress=progress)

    for name, res in out.items():
        res["cost"] = settlement.settle(res, price_import, price_export, dt, run.site)
        res["violations"] = site.check_feasible(res, dt, run.site)
        res["balance_err_kw"] = site.energy_balance_error(res, load, pv, dt, run.site)

    _check_ordering(out)
    return out


def _check_ordering(out: dict[str, dict], tol: float = 1e-6) -> None:
    """Assert B2 <= B3 <= B1 in net cost. A violation means a bug, not a result."""
    cost = {k: v["cost"]["net_cost"] for k, v in out.items()}
    if "B2 perfect foresight" in cost and "B3 rolling MPC" in cost:
        if cost["B2 perfect foresight"] > cost["B3 rolling MPC"] + tol:
            raise AssertionError(
                f"ladder invariant violated: B2 ({cost['B2 perfect foresight']:.4f}) costs more "
                f"than B3 ({cost['B3 rolling MPC']:.4f}); perfect foresight cannot be worse")


def gap_closure(cost_policy: float, cost_b3: float, cost_b2: float) -> float:
    """Fraction of the remaining theoretical headroom that a policy recovers over B3.

    Costs, so lower is better:  (B3 - policy) / (B3 - B2).
    0 = matches MPC, 1 = reaches the perfect-foresight ceiling, <0 = worse than MPC.
    """
    denom = cost_b3 - cost_b2
    return float("nan") if abs(denom) < 1e-12 else (cost_b3 - cost_policy) / denom

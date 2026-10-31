"""Benchmark ladder for the hybrid plant.

B0  unhybridised reference   wind + PV marketed separately, no battery, no curtailment choice
B1  commercial practice      battery on a curtailment-avoidance rule, no price awareness
B2  perfect foresight        LP over the whole period, knows prices and generation exactly
B3  rolling-horizon MPC      the deployable classical optimum, on forecasts
"""
from __future__ import annotations

import time

import numpy as np
import pulp

from .config import MarketConfig, RunConfig
from .market import effective_price, premium_eligible, settle
from .plant import battery_interval, simulate

# Tolerance of the ladder invariant. PuLP reads CBC's solution with about eight significant
# digits, so a rung that re-solves every step (B3) can beat the single plan of B2 by rounding
# alone under perfect forecasts: by a few 1e-9 of the objective, as a test in
# tests/test_ladder_accounting.py shows. The tolerance therefore scales with the objective;
# the floor covers objectives near zero.
_INVARIANT_REL_TOL = 1e-6
_INVARIANT_ABS_TOL = 1e-3          # EUR
# Share of the charged energy that may come from the grid before it counts as bought. With
# grid charging allowed, LP rounding alone imports a tiny share of the charged energy, at any
# window length and plant size; an absolute threshold would refuse that residue on a long
# enough window or a large enough plant.
_GRID_SHARE_TOL = 1e-6


# ------------------------------------------------------------------ B0 / B1
def b0_no_battery(wind: np.ndarray, pv: np.ndarray, run: RunConfig,
                  export_cap: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """The counterfactual: same generation, no storage, curtail only when forced.

    This is what the assets earn WITHOUT hybridisation, and is the commercially relevant
    reference for the question "is the battery worth building?".
    """
    n = len(wind)
    cfg_nobess = run.plant.__class__(**{**run.plant.__dict__, "bess_mw": 0.0,
                                        "bess_mwh": 1e-6})
    # Start the 1e-6 MWh stand-in at its own minimum: from 0, below that minimum, the safety
    # layer forced it to charge, which drew a few 1e-8 MWh from the grid.
    return simulate(wind, pv, np.zeros(n), np.zeros(n), run.dt, cfg_nobess,
                    soc0=cfg_nobess.soc_min, export_cap=export_cap)


def b1_curtailment_avoidance(wind: np.ndarray, pv: np.ndarray, run: RunConfig,
                             export_cap: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Charge whenever generation would otherwise be spilled; discharge whenever there is
    headroom under the connection limit. No price awareness at all.

    This is close to how such plants are actually run today, and it is the validation gate.
    """
    cfg = run.plant
    n = len(wind)
    p_bat = np.zeros(n)
    soc = cfg.soc_init
    for t in range(n):
        avail = wind[t] + pv[t]
        cap = cfg.conn_mw if export_cap is None else min(cfg.conn_mw, export_cap[t])
        lo, hi = battery_interval(soc, run.dt, cfg)
        if avail > cap:                       # would spill: absorb what we can
            p_bat[t] = max(lo, -(avail - cap))
        else:                                 # headroom available: push stored energy out
            p_bat[t] = min(hi, cap - avail)
        from .plant import soc_next
        soc = soc_next(soc, p_bat[t], run.dt, cfg)
    return simulate(wind, pv, p_bat, np.zeros(n), run.dt, cfg, export_cap=export_cap)


# ------------------------------------------------------------------ LP core
def solve_window(wind: np.ndarray, pv: np.ndarray, eff_price: np.ndarray, dt: float,
                 run: RunConfig, soc0: float, terminal_price: float = 0.0,
                 export_cap: np.ndarray | None = None,
                 msg: bool = False) -> tuple[np.ndarray, np.ndarray] | None:
    """Maximise the value of exported energy. Returns (battery power, curtailment fraction).

    Curtailment is a free decision variable: with the negative-price rule active the
    effective price can be negative, and curtailing then is strictly optimal.
    """
    cfg = run.plant
    n = len(wind)
    m = pulp.LpProblem("hybrid", pulp.LpMaximize)

    gen = np.asarray(wind, float) + np.asarray(pv, float)
    cap = np.full(n, cfg.conn_mw) if export_cap is None else np.minimum(export_cap, cfg.conn_mw)

    g = pulp.LpVariable.dicts("g", range(n), lowBound=0)             # generation used
    ch = pulp.LpVariable.dicts("ch", range(n), lowBound=0, upBound=cfg.bess_mw)
    dis = pulp.LpVariable.dicts("dis", range(n), lowBound=0, upBound=cfg.bess_mw)
    soc = pulp.LpVariable.dicts("soc", range(n), lowBound=cfg.soc_min, upBound=cfg.soc_max)
    exp = pulp.LpVariable.dicts("exp", range(n), lowBound=0)

    m += (pulp.lpSum(exp[t] * float(eff_price[t]) * dt for t in range(n))
          - pulp.lpSum((ch[t] + dis[t]) * dt * cfg.c_deg_eur_mwh for t in range(n))
          + terminal_price * soc[n - 1])

    for t in range(n):
        prev = soc0 if t == 0 else soc[t - 1]
        m += soc[t] == prev + (cfg.eta_c * ch[t] - dis[t] / cfg.eta_d) * dt
        m += g[t] <= float(gen[t])                       # cannot use more than is available
        m += exp[t] == g[t] + dis[t] - ch[t]
        m += exp[t] <= float(cap[t])
        if not cfg.grid_charging_allowed:
            m += ch[t] <= g[t]                           # charge only from on-site generation

    m.solve(pulp.PULP_CBC_CMD(msg=msg))
    if pulp.LpStatus[m.status] != "Optimal":
        return None

    p_bat = np.array([(dis[t].value() or 0.0) - (ch[t].value() or 0.0) for t in range(n)])
    used = np.array([g[t].value() or 0.0 for t in range(n)])
    curt_frac = np.where(gen > 1e-9, np.clip(1.0 - used / np.maximum(gen, 1e-9), 0, 1), 0.0)
    return p_bat, curt_frac


def default_terminal_price(eff_price: np.ndarray, run: RunConfig) -> float:
    """Value of energy left in the battery at the horizon end. OFF by default; read this first.

    The idea: stored energy will be exported later at a price the controller can partly
    choose, so value it at the upper quartile of the effective price times the discharge
    efficiency, to stop the battery emptying at every horizon end.

    The evidence says otherwise for this plant. On a receding horizon of 12 h or more,
    re-solved every step, the "horizon end" never arrives: only the first action is applied,
    and the drain it would cause is always one re-solve away. Adding this linear term instead
    made B3 hoard 34 to 38 MWh and fall from 100% of the B1-to-B2 headroom to -138% under
    perfect forecasts, and from 88% to -179% at 15% forecast error.

    The deeper reason is structural. A LINEAR terminal value pushes the end-of-horizon state of
    charge to a bound, because its slope is the right marginal value at one SoC level only. If
    a terminal value is needed (short horizons, long storage cycles), it should be concave and
    SoC-dependent: piecewise linear, fitted from perfect-foresight shadow prices on a
    separate training window. The same finding holds for Project 01.
    """
    if not run.terminal_value:
        return 0.0
    if run.terminal_price is not None:
        return run.terminal_price
    return float(np.percentile(eff_price, 75)) * run.plant.eta_d


# ------------------------------------------------------------------ B2 / B3
def b2_perfect(wind, pv, eff_price, run: RunConfig,
               export_cap=None) -> dict[str, np.ndarray]:
    sol = solve_window(wind, pv, eff_price, run.dt, run, run.plant.soc_init,
                       terminal_price=0.0, export_cap=export_cap)
    if sol is None:
        raise RuntimeError("B2 infeasible")
    p_bat, curt = sol
    return simulate(wind, pv, p_bat, curt, run.dt, run.plant, export_cap=export_cap)


def b3_rolling(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run: RunConfig,
               export_cap=None, resolve_every: int = 1,
               progress: bool = False, premium=None) -> dict[str, np.ndarray]:
    """Rolling-horizon MPC on forecast generation and prices.

    wind, pv: available generation per step in MW. eff_price: effective price per step in
    EUR/MWh, and price_fc its forecast (`make_forecasts`). wind_fc, pv_fc: generation forecasts.
    premium: None, or (rate, eligible) per step: the market premium B3 knows when it decides in
        that step (EUR/MWh, e.g. `market.planning_rates`) and whether the premium is payable
        there. eff_price and price_fc then carry each step's own rate. A plan made in step t
        prices its whole horizon at rate[t]: a later step's rate can be one B3 cannot know yet
        at t, such as the realised value of the month t lies in, which sets next month's rate.
    Returns the dispatch arrays of `plant.simulate`, plus "solve_time_s" per re-solve.
    """
    cfg = run.plant
    n = len(wind)
    soc = cfg.soc_init
    p_applied = np.zeros(n)
    curt_applied = np.zeros(n)
    solve_times: list[float] = []
    plan = None
    offset = 0

    for t in range(n):
        if t % resolve_every == 0 or plan is None:
            h = min(run.horizon_steps, n - t)
            w = np.concatenate([[wind[t]], wind_fc[t + 1:t + h]])
            s = np.concatenate([[pv[t]], pv_fc[t + 1:t + h]])
            pr = np.concatenate([[eff_price[t]], price_fc[t + 1:t + h]])
            if premium is not None:
                rate, eligible = premium
                pr = pr + np.where(eligible[t:t + h], rate[t] - rate[t:t + h], 0.0)
            ec = None if export_cap is None else export_cap[t:t + h]
            t0 = time.perf_counter()
            sol = solve_window(w, s, pr, run.dt, run, soc,
                               terminal_price=default_terminal_price(pr, run),
                               export_cap=ec)
            solve_times.append(time.perf_counter() - t0)
            plan = sol if sol is not None else (np.zeros(h), np.zeros(h))
            offset = t

        k = t - offset
        pb = float(plan[0][k]) if k < len(plan[0]) else 0.0
        cf = float(plan[1][k]) if k < len(plan[1]) else 0.0
        # Execute planned curtailment only where it is economically motivated. The plan's
        # curtailment fraction was computed on FORECAST generation and includes curtailment
        # the connection limit forced in the plan; applying that fraction to the true
        # generation discarded energy the connection could have taken. With a non-negative
        # effective price, exporting more is never worse, so the point-of-interconnection
        # enforcement in `dispatch_step` handles the cap on its own.
        if eff_price[t] >= 0.0:
            cf = 0.0

        from .plant import dispatch_step
        r = dispatch_step(float(wind[t]), float(pv[t]), soc, pb, cf, run.dt, cfg,
                          None if export_cap is None else float(export_cap[t]))
        p_applied[t], curt_applied[t] = r["p_bat"], cf
        soc = r["soc"]
        if progress and t % max(1, n // 10) == 0:
            print(f"    B3 {100 * t / n:3.0f}%", flush=True)

    res = simulate(wind, pv, p_applied, curt_applied, run.dt, cfg, export_cap=export_cap)
    res["solve_time_s"] = np.array(solve_times)
    return res


# ------------------------------------------------------------------ forecasts / driver
def ar_noise(n, rng, rho):
    z = rng.standard_normal(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * z[i]
    return z


def make_forecasts(wind, pv, eff_price, run: RunConfig, rng):
    n = len(wind)
    s, r = run.forecast_sigma, run.forecast_rho
    return (np.clip(wind * (1 + s * ar_noise(n, rng, r)), 0, None),
            np.clip(pv * (1 + s * ar_noise(n, rng, r)), 0, None),
            eff_price + s * np.std(eff_price) * ar_noise(n, rng, r))


def _refuse_premium_on_grid_energy(name: str, res: dict[str, np.ndarray],
                                   premium_rate: float | np.ndarray, market: MarketConfig,
                                   dt: float) -> None:
    """Stop the run when a rung charged grid energy while the market premium is paid.

    The premium follows renewable energy into the battery (§ 19 (3) EEG 2023); energy bought
    from the grid carries none when it is sold again. `settle` pays the premium on all
    eligible export, and the ladder does not track which part of the stored energy came from
    the grid, so a rung that buys, stores and resells would be paid the premium on energy it
    never generated. No shipped rung buys grid energy; this guards a controller that would.

    name: rung name, for the message. res: the rung's dispatch, with "import" and "p_ch"
    per step in MW (the plant imports only to charge, so import <= p_ch).
    premium_rate: market premium in EUR/MWh, one rate or one per step.
    market: the run's market settings. dt: step length in hours.
    Raises NotImplementedError when more than _GRID_SHARE_TOL of the charged energy came
    from the grid while a premium is paid.
    """
    bought = float(np.sum(res["import"]) * dt)
    charged = float(np.sum(res["p_ch"]) * dt)
    premium_paid = market.eeg_enabled and bool(np.any(np.asarray(premium_rate) > 0.0))
    if premium_paid and bought > _GRID_SHARE_TOL * charged:
        raise NotImplementedError(
            f"{name} charged {charged:,.2f} MWh, {bought:,.2f} MWh of it from the grid, while "
            "the market premium is paid; the premium is due on renewable energy only, and the "
            "ladder cannot tell grid energy from renewable energy in the battery")


def run_ladder(wind, pv, price, run: RunConfig, premium_rate: float,
               rebap=None, export_cap=None, include_b3=True,
               progress=False, schedule=None, b3_premium_rate=None) -> dict[str, dict]:
    """Run every rung on the same window, settle it, and check the ladder invariant.

    wind, pv: available generation per step in MW, e.g. np.array([31.2, 29.8, ...]).
    price: day-ahead price per step in EUR/MWh.
    premium_rate: the market premium every rung is settled on, EUR/MWh, one rate or one per
        step: the realised value (`market.premium_rates`). B2 plans on it, as perfect
        foresight may; B0 and B1 read no prices.
    b3_premium_rate: the premium B3 plans on, one rate or one per step: in each step the rate
        B3 knows when it decides there, e.g. `market.planning_rates`, the previous month's
        realised rate. None plans B3 on premium_rate as well.
    rebap, schedule: imbalance price in EUR/MWh and the export the plant committed to, in MW,
        both per step. Each rung's deviation from the schedule is settled at reBAP only when
        both are given; no rung commits a schedule of its own, so reBAP alone settles nothing.
        The settlement's imbalance_cost is the deviation priced at reBAP minus spot.
    export_cap: per-step export limit in MW (a redispatch order), or None for the connection.
    Returns {rung name: dispatch arrays plus "cost" (the settlement) and "violations"}.
    Raises AssertionError when a rung beats perfect foresight B2 on revenue before imbalance,
    and NotImplementedError when a rung charged grid energy while the market premium is paid.
    """
    from .plant import check_feasible

    rng = np.random.default_rng(run.seed)
    # The step length counts the hours of a negative-price run, so planning must use the one
    # the settlement uses; without it, B2 and B3 planned every step as a quarter-hour.
    eff = effective_price(price, run.market, premium_rate, run.dt)

    out = {
        "B0 no battery": b0_no_battery(wind, pv, run, export_cap),
        "B1 curtailment avoidance": b1_curtailment_avoidance(wind, pv, run, export_cap),
        "B2 perfect foresight": b2_perfect(wind, pv, eff, run, export_cap),
    }
    if include_b3:
        # B3 plans on the premium it can know, not on the realised value it is settled on: a
        # month's market value is known only once the month is over.
        plan = premium_rate if b3_premium_rate is None else b3_premium_rate
        eff_b3 = effective_price(price, run.market, plan, run.dt)
        wind_fc, pv_fc, price_fc = make_forecasts(wind, pv, eff_b3, run, rng)
        eligible = (premium_eligible(price, run.market, run.dt) if run.market.eeg_enabled
                    else np.zeros(len(price), dtype=bool))
        rate = np.broadcast_to(np.asarray(plan, dtype=float), (len(price),))
        out["B3 rolling MPC"] = b3_rolling(wind, pv, eff_b3, wind_fc, pv_fc, price_fc, run,
                                           export_cap, progress=progress,
                                           premium=(rate, eligible))

    # Single-price settlement pays spot on the committed schedule and reBAP on the deviation.
    # settle() pays spot on the whole export, so it must price the deviation at the spread,
    # reBAP minus spot; handed reBAP itself, it paid the deviation twice.
    spread = None if rebap is None else (np.asarray(rebap, dtype=float)
                                         - np.asarray(price, dtype=float))
    for name, r in out.items():
        _refuse_premium_on_grid_energy(name, r, premium_rate, run.market, run.dt)
        r["cost"] = settle(r, price, run.dt, run.plant, run.market,
                           rebap=spread, schedule=schedule, premium_rate=premium_rate)
        r["violations"] = check_feasible(
            r, run.plant if "no battery" not in name
            else run.plant.__class__(**{**run.plant.__dict__, "bess_mw": 0.0,
                                        "bess_mwh": 1e-6}))

    # Perfect foresight cannot lose to ANY other rung. Checking only B3 would have missed the
    # premium-feedback bug, which showed up as B2 scoring below the do-nothing baseline.
    # B2 is the ceiling of what it optimises, and imbalance lies outside that: the committed
    # schedule and reBAP are not in its objective, so a rung that deviates the lucky way can
    # out-earn it there. The check therefore compares revenue before imbalance.
    before = {name: r["cost"]["net_revenue"] + r["cost"]["imbalance_cost"]
              for name, r in out.items()}
    b2 = before["B2 perfect foresight"]
    tol = max(_INVARIANT_ABS_TOL, _INVARIANT_REL_TOL * abs(b2))
    for name, rev in before.items():
        if name != "B2 perfect foresight" and rev > b2 + tol:
            raise AssertionError(
                f"ladder invariant violated: {name} ({rev:,.0f}) beats perfect foresight "
                f"B2 ({b2:,.0f}) before imbalance - impossible, so something is wrong")
    return out

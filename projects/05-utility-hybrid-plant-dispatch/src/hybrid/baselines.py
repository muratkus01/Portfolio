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

from .config import RunConfig
from .market import effective_price, premium_eligible, settle
from .plant import battery_interval, simulate


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
    return simulate(wind, pv, np.zeros(n), np.zeros(n), run.dt, cfg_nobess,
                    soc0=0.0, export_cap=export_cap)


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
    """Value of energy left in the battery at the horizon end.

    Stored energy will be exported later, at a price the controller can partly choose, so the
    upper quartile of the effective price - discounted by discharge efficiency - is a better
    estimate than the median. Without this the battery empties at every horizon end.
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
               export_cap=None, resolve_every: int = 4,
               progress: bool = False) -> dict[str, np.ndarray]:
    """Rolling-horizon MPC on forecast generation and prices."""
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
            ec = None if export_cap is None else export_cap[t:t + h]
            t0 = time.perf_counter()
            tp = default_terminal_price(pr, run) if (t + h < n) else 0.0
            sol = solve_window(w, s, pr, run.dt, run, soc,
                               terminal_price=tp,
                               export_cap=ec)
            solve_times.append(time.perf_counter() - t0)
            plan = sol if sol is not None else (np.zeros(h), np.zeros(h))
            offset = t

        k = t - offset
        pb = float(plan[0][k]) if k < len(plan[0]) else 0.0
        cf_raw = float(plan[1][k]) if k < len(plan[1]) else 0.0
        cf = cf_raw if eff_price[t] < 0 else 0.0

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


def run_ladder(wind, pv, price, run: RunConfig, premium_rate: float,
               rebap=None, export_cap=None, include_b3=True,
               progress=False) -> dict[str, dict]:
    from .plant import check_feasible

    rng = np.random.default_rng(run.seed)
    eff = effective_price(price, run.market, premium_rate)
    wind_fc, pv_fc, price_fc = make_forecasts(wind, pv, eff, run, rng)

    out = {
        "B0 no battery": b0_no_battery(wind, pv, run, export_cap),
        "B1 curtailment avoidance": b1_curtailment_avoidance(wind, pv, run, export_cap),
        "B2 perfect foresight": b2_perfect(wind, pv, eff, run, export_cap),
    }
    if include_b3:
        out["B3 rolling MPC"] = b3_rolling(wind, pv, eff, wind_fc, pv_fc, price_fc, run,
                                           export_cap, progress=progress)

    for name, r in out.items():
        sched = r["export"] if rebap is None else None
        r["cost"] = settle(r, price, run.dt, run.plant, run.market,
                           rebap=rebap, schedule=sched, premium_rate=premium_rate)
        r["violations"] = check_feasible(
            r, run.plant if "no battery" not in name
            else run.plant.__class__(**{**run.plant.__dict__, "bess_mw": 0.0,
                                        "bess_mwh": 1e-6}))

    # Perfect foresight cannot lose to ANY other rung. Checking only B3 would have missed the
    # premium-feedback bug, which showed up as B2 scoring below the do-nothing baseline.
    b2 = out["B2 perfect foresight"]["cost"]["net_revenue"]
    for name, r in out.items():
        if name != "B2 perfect foresight" and r["cost"]["net_revenue"] > b2 + 1e-3:
            raise AssertionError(
                f"ladder invariant violated: {name} ({r['cost']['net_revenue']:,.0f}) beats "
                f"perfect foresight B2 ({b2:,.0f}) - impossible, so something is wrong")
    return out

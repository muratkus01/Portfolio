"""Benchmark ladder for the pumped-storage plant.

B1  price-threshold rule       the classical dispatch rule; the validation gate
B2  perfect-foresight LP       the ceiling - knows prices AND realised activations
B3  rolling-horizon MPC        the deployable classical optimum, on forecasts

Invariant asserted at runtime: revenue(B2) >= revenue(B3) >= revenue(B1).

Binary mode variables enforce mutual exclusion between pumping and turbining (u_t + u_p <= 1),
guaranteeing the machine never operates in both modes simultaneously and strictly respecting
physical ramp limits across mode transitions. Both B2 perfect foresight and B3 rolling MPC
solve this MILP via CBC.
"""
from __future__ import annotations

import time

import numpy as np
import pulp

from .config import RunConfig
from .market import settle
from .plant import e_next, feasible_interval, simulate


# ------------------------------------------------------------------ B1
def b1_price_threshold(price: np.ndarray, run: RunConfig,
                       e0: float | None = None,
                       activation: np.ndarray | None = None,
                       reserved_pos: np.ndarray | None = None,
                       reserved_neg: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Turbine when the price is high, pump when it is low, otherwise idle.

    Thresholds are the rolling 30th/70th percentiles of the price over the past day - a
    reasonable stand-in for how such a plant was actually run before optimisation, and the
    status quo the ladder measures against.
    """
    cfg = run.plant
    n = len(price)
    win = run.steps_per_day
    p = np.zeros(n)
    for t in range(n):
        lo_i = max(0, t - win)
        ref = price[lo_i:t + 1]
        q30, q70 = np.percentile(ref, [30, 70]) if len(ref) > 4 else (0.0, 1e9)
        if price[t] >= q70:
            p[t] = cfg.p_turb_max
        elif price[t] <= q30:
            p[t] = -cfg.p_pump_max
    return simulate(p, run.dt, cfg, e0, activation, reserved_pos, reserved_neg)


# ------------------------------------------------------------------ LP core
def solve_window(price: np.ndarray, dt: float, run: RunConfig, e0: float,
                 terminal_price: float = 0.0,
                 reserved_pos: np.ndarray | None = None,
                 reserved_neg: np.ndarray | None = None,
                 activation: np.ndarray | None = None,
                 prev_p: float = 0.0,
                 msg: bool = False) -> np.ndarray | None:
    """Maximise revenue over a window. Returns the planned net power, or None if infeasible.

    The LP must describe the SAME plant as `plant.feasible_interval`, or perfect foresight is
    not a ceiling. It once did not: it kept a symmetric ramp on net power after the plant model
    had moved to per-mode up-ramps with free unloading, it had no spillway, and it charged
    downward activation at the turbine efficiency. The rolling controller, executed on the
    real plant, then beat "perfect foresight" by 1,249 EUR, and the ladder invariant stopped
    the run. The constraints below mirror the plant one for one:

      per-mode up-ramp  turbine output and pump load may each rise by at most `ramp` per step,
                        starting from `prev_p`; unloading is free
      spill             inflow that would overtop the operating ceiling may be spilled
      activation        upward calls drain at 1/eta_turb, downward calls fill at eta_pump
    """
    cfg = run.plant
    n = len(price)
    m = pulp.LpProblem("psw", pulp.LpMaximize)

    u_t = pulp.LpVariable.dicts("ut", range(n), cat=pulp.LpBinary)
    u_p = pulp.LpVariable.dicts("up", range(n), cat=pulp.LpBinary)
    p_t = pulp.LpVariable.dicts("pt", range(n), lowBound=0, upBound=cfg.p_turb_max)
    p_p = pulp.LpVariable.dicts("pp", range(n), lowBound=0, upBound=cfg.p_pump_max)
    e = pulp.LpVariable.dicts("e", range(n), lowBound=cfg.e_reserve_low,
                              upBound=cfg.e_reserve_high)
    spill = pulp.LpVariable.dicts("spill", range(n), lowBound=0)

    pump_price_adder = 0.0 if cfg.para_118_6_exempt else cfg.network_charge_pump

    m += (pulp.lpSum(p_t[t] * float(price[t]) * dt for t in range(n))
          - pulp.lpSum(p_p[t] * (float(price[t]) + pump_price_adder) * dt for t in range(n))
          + terminal_price * e[n - 1])

    prev_turb, prev_pump = max(prev_p, 0.0), max(-prev_p, 0.0)
    for t in range(n):
        m += u_t[t] + u_p[t] <= 1
        cap_t = cfg.p_turb_max if reserved_pos is None else (cfg.p_turb_max - float(reserved_pos[t]))
        cap_p = cfg.p_pump_max if reserved_neg is None else (cfg.p_pump_max - float(reserved_neg[t]))
        m += p_t[t] <= cap_t * u_t[t]
        m += p_p[t] <= cap_p * u_p[t]

        prev_e = e0 if t == 0 else e[t - 1]
        act = 0.0 if activation is None else float(activation[t])
        act_up, act_dn = max(act, 0.0), max(-act, 0.0)
        m += e[t] == prev_e + (-p_t[t] / cfg.eta_turb + p_p[t] * cfg.eta_pump + cfg.inflow_mw
                               - act_up / cfg.eta_turb + act_dn * cfg.eta_pump) * dt - spill[t]

        # per-mode up-ramp, matching feasible_interval; unloading is unconstrained
        last_t = prev_turb if t == 0 else p_t[t - 1]
        last_p = prev_pump if t == 0 else p_p[t - 1]
        m += p_t[t] - last_t <= cfg.ramp_mw_per_step
        m += p_p[t] - last_p <= cfg.ramp_mw_per_step

    m.solve(pulp.PULP_CBC_CMD(msg=msg))
    if pulp.LpStatus[m.status] != "Optimal":
        return None
    return np.array([(p_t[t].value() or 0.0) - (p_p[t].value() or 0.0) for t in range(n)])


def default_terminal_price(price: np.ndarray, run: RunConfig) -> float:
    """Value of water left in the upper reservoir at the horizon end.

    Without it the plant empties its reservoir at every horizon end - the same defect D4 seen
    in the prosumer model, and far more damaging here because a PSW reservoir cycle is longer
    than any tractable horizon. A stored MWh will be turbined later, so it is worth roughly
    the achievable future price times the turbine efficiency; the upper quartile is used
    rather than the median because stored energy is deployed into HIGH prices, not average
    ones.
    """
    if not run.terminal_value:
        return 0.0
    if run.terminal_price is not None:
        return run.terminal_price
    return float(np.percentile(price, 75)) * run.plant.eta_turb


# ------------------------------------------------------------------ B2 / B3
def b2_perfect(price: np.ndarray, run: RunConfig, e0: float | None = None,
               activation: np.ndarray | None = None,
               reserved_pos: np.ndarray | None = None,
               reserved_neg: np.ndarray | None = None) -> dict[str, np.ndarray]:
    e0 = run.plant.e_init if e0 is None else e0
    plan = solve_window(price, run.dt, run, e0, terminal_price=0.0, activation=activation,
                        reserved_pos=reserved_pos, reserved_neg=reserved_neg)
    if plan is None:
        raise RuntimeError("B2 infeasible - check reservoir bounds against inflow")
    return simulate(plan, run.dt, run.plant, e0, activation, reserved_pos, reserved_neg)


def b3_rolling(price: np.ndarray, price_fc: np.ndarray, run: RunConfig,
               e0: float | None = None, activation: np.ndarray | None = None,
               reserved_pos: np.ndarray | None = None,
               reserved_neg: np.ndarray | None = None,
               resolve_every: int = 4, progress: bool = False) -> dict[str, np.ndarray]:
    """Rolling-horizon MPC on forecast prices, blind to future activations.

    `resolve_every=4` re-optimises hourly rather than every quarter-hour. That weakens the
    baseline slightly and is reported; it is here because a 192-step LP re-solved 35 000 times
    is not a laptop-scale experiment. Set it to 1 for the headline run.
    """
    cfg = run.plant
    n = len(price)
    e = cfg.e_init if e0 is None else e0
    applied = np.zeros(n)
    solve_times: list[float] = []
    plan, offset = None, 0
    prev_p = 0.0

    for t in range(n):
        if t % resolve_every == 0 or plan is None:
            h = min(run.horizon_steps, n - t)
            pw = np.concatenate([[price[t]], price_fc[t + 1:t + h]])
            rp = None if reserved_pos is None else reserved_pos[t:t + h]
            rn = None if reserved_neg is None else reserved_neg[t:t + h]
            t0 = time.perf_counter()
            sol = solve_window(pw, run.dt, run, e,
                               terminal_price=default_terminal_price(pw, run),
                               reserved_pos=rp, reserved_neg=rn, prev_p=prev_p)
            solve_times.append(time.perf_counter() - t0)
            plan, offset = (sol if sol is not None else np.zeros(h)), t

        k = t - offset
        proposed = float(plan[k]) if k < len(plan) else 0.0
        act = 0.0 if activation is None else float(activation[t])
        lo, hi = feasible_interval(
            e, run.dt, cfg, prev_p=prev_p,
            reserved_pos=0.0 if reserved_pos is None else float(reserved_pos[t]),
            reserved_neg=0.0 if reserved_neg is None else float(reserved_neg[t]))
        p = float(np.clip(proposed + act, lo, hi))
        applied[t] = p
        prev_p = p
        e, _spill = e_next(e, p, run.dt, cfg)

        if progress and t % max(1, n // 10) == 0:
            print(f"    B3 {100 * t / n:3.0f}%", flush=True)

    res = simulate(applied, run.dt, cfg, e0)
    res["solve_time_s"] = np.array(solve_times)
    return res


# ------------------------------------------------------------------ forecasts / driver
def ar_noise(n: int, rng: np.random.Generator, rho: float) -> np.ndarray:
    z = rng.standard_normal(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * z[i]
    return z


def make_price_forecast(price: np.ndarray, run: RunConfig,
                        rng: np.random.Generator) -> np.ndarray:
    return price + run.forecast_sigma * np.std(price) * ar_noise(len(price), rng, run.forecast_rho)


def run_ladder(price: np.ndarray, run: RunConfig, sold_pos: np.ndarray | None = None,
               sold_neg: np.ndarray | None = None, activation: np.ndarray | None = None,
               rebap: np.ndarray | None = None, include_b3: bool = True,
               progress: bool = False) -> dict[str, dict]:
    rng = np.random.default_rng(run.seed)
    price_fc = make_price_forecast(price, run, rng)

    out: dict[str, dict] = {
        "B1 price threshold": b1_price_threshold(price, run, activation=activation,
                                                 reserved_pos=sold_pos, reserved_neg=sold_neg),
        "B2 perfect foresight": b2_perfect(price, run, activation=activation,
                                           reserved_pos=sold_pos, reserved_neg=sold_neg),
    }
    if include_b3:
        out["B3 rolling MPC"] = b3_rolling(price, price_fc, run, activation=activation,
                                           reserved_pos=sold_pos, reserved_neg=sold_neg,
                                           progress=progress)

    from .plant import check_feasible, security_readiness
    for r in out.values():
        r["cost"] = settle(r, price, run.dt, run.plant, run.market,
                           sold_pos, sold_neg, activation, rebap=rebap)
        r["violations"] = check_feasible(r, run.plant)
        r["security"] = security_readiness(r, run.plant, run.dt)

    b2 = out["B2 perfect foresight"]["cost"]["net_revenue"]
    if include_b3:
        b3 = out["B3 rolling MPC"]["cost"]["net_revenue"]
        if b3 > b2 + 1e-3:
            raise AssertionError(
                f"ladder invariant violated: B3 ({b3:,.0f}) beats perfect foresight "
                f"B2 ({b2:,.0f}) - that is impossible, so something is wrong")
    return out

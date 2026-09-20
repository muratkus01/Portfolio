"""Pumped-storage plant physics and the safety layer.

Shared by every rung of the ladder, so no controller can be evaluated against different
physics than another. Water-law reservoir limits live here, enforced by projection - never as
a reward penalty, because a controller that can be paid to violate a water permit is not a
controller anyone will install.
"""
from __future__ import annotations

import numpy as np

from .config import PlantConfig


def split_power(p: float) -> tuple[float, float]:
    """(turbine magnitude, pump magnitude) from a signed power."""
    return max(p, 0.0), max(-p, 0.0)


def efficiency(cfg: PlantConfig, e_res: float) -> tuple[float, float]:
    """Turbine and pump efficiency, optionally head-dependent.

    With `head_sensitivity = 0` this is the constant-head idealisation the MILP linearises to.
    With it non-zero, the simulator uses the varying value while the MILP still sees the
    constant one - which is exactly the kind of structural gap a learned policy can exploit,
    and is therefore reported as an ablation rather than assumed away.
    """
    if cfg.head_sensitivity == 0.0:
        return cfg.eta_turb, cfg.eta_pump
    fill = (e_res - cfg.e_min) / max(cfg.e_max - cfg.e_min, 1e-9)
    d = cfg.head_sensitivity * (fill - 0.5)
    return (float(np.clip(cfg.eta_turb + d, 0.5, 0.98)),
            float(np.clip(cfg.eta_pump + d, 0.5, 0.98)))


def e_next(e_res: float, p: float, dt: float, cfg: PlantConfig) -> tuple[float, float]:
    """Reservoir energy balance including natural inflow. Returns (new energy, spill).

    Turbining at p MW draws p/eta_turb MWh-equivalent from the reservoir; pumping at p MW adds
    p*eta_pump. The asymmetry is where the round-trip loss lives.

    **Spill.** If inflow would carry the reservoir above its operating ceiling, the excess is
    spilled rather than forcing the plant to generate. Without this the reservoir bound
    silently overrides the ramp limit, and the safety layer leaks up-ramp violations - which
    is exactly what the first version of this model did. A real plant has a spillway; the
    model needs one too, and spilled energy is tracked because it is lost revenue.
    """
    p_t, p_p = split_power(p)
    eta_t, eta_p = efficiency(cfg, e_res)
    e = e_res + (-p_t / eta_t + p_p * eta_p + cfg.inflow_mw) * dt
    spill = max(0.0, e - cfg.e_reserve_high)
    return e - spill, spill


def feasible_interval(e_res: float, dt: float, cfg: PlantConfig, prev_p: float = 0.0,
                      reserved_pos: float = 0.0, reserved_neg: float = 0.0,
                      redispatch_cap: float | None = None) -> tuple[float, float]:
    """Interval of feasible net power this step.

    Constraint precedence matters, and getting it wrong is how a safety layer silently leaks
    violations. Two rules, both physical:

    **The reservoir wins over the ramp.** Water-law reservoir limits are hard; the ramp limit
    is a machine comfort constraint. When the two conflict - the reservoir demands more
    turbining than the up-ramp allows - the plant ramps faster, it does not overtop its
    permit. So the ramp interval is intersected with the reservoir interval, and if the
    intersection is empty the reservoir interval wins.

    **The down-ramp is not binding.** Reducing output is always possible (in the limit, trip
    the unit); it is *increasing* output that is rate-limited by the hydraulic system. Imposing
    a symmetric hard down-ramp makes the problem spuriously infeasible whenever the reservoir
    forces a fast reduction. Only the up-ramp is enforced, and `check_feasible` tests only
    that direction.

    Then, in order: headroom reserved for sold balancing capacity - selling POS aFRR keeps
    `reserved_pos` MW of upward capability out of energy trading, and that coupling IS the
    multi-market problem - and finally an active redispatch order, which overrides everything
    commercial.
    """
    eta_t, eta_p = efficiency(cfg, e_res)

    # --- reservoir interval (hard) ---
    # turbining down to the floor:  e - p/eta_t*dt + inflow*dt >= e_reserve_low
    res_hi = (e_res - cfg.e_reserve_low + cfg.inflow_mw * dt) * eta_t / dt
    # pumping up to the ceiling:    e + p*eta_p*dt + inflow*dt <= e_reserve_high
    res_lo = -(cfg.e_reserve_high - e_res - cfg.inflow_mw * dt) / (eta_p * dt)
    # The reservoir RESTRICTS an action, it never FORCES the opposite one: a full reservoir
    # stops you pumping (the excess spills), an empty one stops you turbining. Clipping the
    # bounds around zero guarantees the interval always contains 0, so it is never empty and
    # the up-ramp limit can always be honoured.
    res_lo = float(np.clip(res_lo, -cfg.p_pump_max, 0.0))
    res_hi = float(np.clip(res_hi, 0.0, cfg.p_turb_max))

    # --- ramp: per MODE, and only in the increasing direction ---
    # Defining the ramp on signed NET power is wrong and was the second bug here. Backing off
    # the pumps because the reservoir is filling shows up as a large positive change in net
    # power, but it is a REDUCTION in machine loading and is always physically possible.
    # The rate limit belongs to each mode separately: turbine output may rise by at most
    # `ramp` per step, pump load may rise by at most `ramp` per step, and either may fall
    # freely.
    prev_t, prev_p_pump = max(prev_p, 0.0), max(-prev_p, 0.0)
    hi = min(res_hi, prev_t + cfg.ramp_mw_per_step)
    lo = max(res_lo, -(prev_p_pump + cfg.ramp_mw_per_step))
    if hi < lo:
        hi = lo

    # --- commitments ---
    hi = min(hi, cfg.p_turb_max - reserved_pos)
    lo = max(lo, -cfg.p_pump_max + reserved_neg)
    if redispatch_cap is not None:
        hi = min(hi, redispatch_cap)

    if hi < lo:
        lo = hi
    return lo, hi


def project(p_proposed: float, e_res: float, dt: float, cfg: PlantConfig, **kw) -> float:
    lo, hi = feasible_interval(e_res, dt, cfg, **kw)
    return float(np.clip(p_proposed, lo, hi))


def simulate(p: np.ndarray, dt: float, cfg: PlantConfig, e0: float | None = None,
             activation: np.ndarray | None = None,
             reserved_pos: np.ndarray | None = None,
             reserved_neg: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Run a dispatch trajectory. `activation` is signed MW of called balancing energy.

    Activation is obligatory once capacity is sold, so it is added to the commercial setpoint
    and the RESULT is what moves the reservoir. That is the mechanism by which selling
    capacity makes the reservoir state stochastic - the property a point-forecast MPC
    systematically mis-values.

    `reserved_pos` / `reserved_neg` are the aFRR capacities sold per step. The COMMERCIAL
    setpoint must leave that headroom free; activation may then use it. Every rung of the
    ladder passes the same reservations, so none of them can trade through capacity it has
    already sold. An earlier version applied them only to B3, which let B1 use headroom it had
    sold and inflated the B2 ceiling: B3 then topped out at 83% of the headroom even with
    perfect price forecasts.
    """
    n = len(p)
    e = np.empty(n)
    spill = np.zeros(n)
    p_real = np.empty(n)
    p_sched = np.empty(n)
    s = cfg.e_init if e0 is None else e0
    for t in range(n):
        prev = p_real[t - 1] if t else 0.0
        rp = 0.0 if reserved_pos is None else float(reserved_pos[t])
        rn = 0.0 if reserved_neg is None else float(reserved_neg[t])
        lo_c, hi_c = feasible_interval(s, dt, cfg, prev_p=prev, reserved_pos=rp, reserved_neg=rn)
        pt = float(np.clip(float(p[t]), lo_c, hi_c))
        p_sched[t] = pt
        pt += 0.0 if activation is None else float(activation[t])
        lo, hi = feasible_interval(s, dt, cfg, prev_p=prev)
        pt = float(np.clip(pt, lo, hi))
        p_real[t] = pt
        s, spill[t] = e_next(s, pt, dt, cfg)
        e[t] = s
    p_t, p_p = np.maximum(p_real, 0.0), np.maximum(-p_real, 0.0)
    return {
        "e_res": e, "p": p_real, "p_turb": p_t, "p_pump": p_p, "spill_mwh": spill,
        "p_sched": p_sched,
        "mode_changes": reversals(p_real),
    }


def reversals(p: np.ndarray, tol: float = 1e-6) -> np.ndarray:
    """Boolean per step: did the machine reverse direction (pump <-> turbine) here?

    Idle steps carry the last direction forward, so pump -> idle -> turbine is one reversal and
    turbine -> idle -> turbine is none. That is the physically expensive event for a reversible
    pump-turbine (the rotor changes direction), and it is what `PlantConfig.mode_change_cost`
    is documented to price. An earlier version counted every change among pump, idle and
    turbine, so merely pausing the turbine was charged as a mode change, and no optimiser could
    represent that cost without extra binaries.
    """
    sign = np.where(p > tol, 1, np.where(p < -tol, -1, 0))
    last = np.zeros_like(sign)
    cur = 0
    for i, v in enumerate(sign):
        if v != 0:
            cur = v
        last[i] = cur
    prev = np.concatenate([[0], last[:-1]])
    return (last != 0) & (prev != 0) & (last != prev)


def check_feasible(res: dict[str, np.ndarray], cfg: PlantConfig,
                   tol: float = 1e-6) -> dict[str, int]:
    """Violation counts. Expected all-zero: constraints are enforced, not priced."""
    p = res["p"]
    return {
        "reservoir_low": int(np.sum(res["e_res"] < cfg.e_min - tol)),
        "reservoir_high": int(np.sum(res["e_res"] > cfg.e_max + tol)),
        "water_law_low": int(np.sum(res["e_res"] < cfg.e_reserve_low - tol)),
        "water_law_high": int(np.sum(res["e_res"] > cfg.e_reserve_high + tol)),
        "turbine_rating": int(np.sum(p > cfg.p_turb_max + tol)),
        "pump_rating": int(np.sum(-p > cfg.p_pump_max + tol)),
        # Ramp is per mode and only in the increasing direction: loading a machine up is
        # rate-limited, unloading it is not. See `feasible_interval`.
        "turbine_ramp_up": int(np.sum(
            np.diff(res["p_turb"], prepend=0.0) > cfg.ramp_mw_per_step + 1e-3)),
        "pump_ramp_up": int(np.sum(
            np.diff(res["p_pump"], prepend=0.0) > cfg.ramp_mw_per_step + 1e-3)),
    }


def security_readiness(res: dict[str, np.ndarray], cfg: PlantConfig,
                       dt: float) -> dict[str, float]:
    """How useful the plant is to the system, independent of what it earned.

    The grid-security objective. Three components, each normalised to [0, 1]:

      up_reserve   - upward capability still available, averaged over time
      down_reserve - downward (pumping) capability still available
      duration     - how long the reservoir could sustain full upward output

    A plant sitting at maximum output with an empty reservoir earns well and is worthless to
    the system in a stress event. Making that trade-off explicit and measurable is the point
    of this project, and it is what the lambda sweep prices.
    """
    p = res["p"]
    e = res["e_res"]
    up = np.clip((cfg.p_turb_max - p) / cfg.p_turb_max, 0, 1).mean()
    down = np.clip((cfg.p_pump_max + p) / cfg.p_pump_max, 0, 1).mean()
    usable = np.clip(e - cfg.e_reserve_low, 0, None)
    hours_at_full = usable / max(cfg.p_turb_max / cfg.eta_turb, 1e-9)
    duration = np.clip(hours_at_full / 4.0, 0, 1).mean()      # 4 h = "fully ready"
    return {
        "up_reserve": float(up),
        "down_reserve": float(down),
        "duration": float(duration),
        "index": float((up + down + duration) / 3.0),
    }

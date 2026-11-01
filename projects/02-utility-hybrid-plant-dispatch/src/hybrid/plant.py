"""Hybrid plant physics and safety layer.

The plant is: wind + PV + battery, all behind one point of interconnection whose export limit
is smaller than the installed generation. Every step resolves

    export = wind_out + pv_out + p_bat   <=   conn_mw

with curtailment as an explicit decision variable, because *self-chosen* curtailment and
*ordered* (redispatch) curtailment settle differently and must be accounted separately.

An aFRR reservation (`Reservation`) restricts the battery and the export and never forces
them, the rule of the archived pumped-storage project: it keeps power headroom, the
connection headroom of positive reserve, and the energy band of each reserved direction as
far as physics allows. What a step could not hold is measured as reserve not held, for the
settlement to charge; it is not counted as a broken limit.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from .config import PlantConfig
from .reserve import ReserveRule

# Reserve not held below this, in MW, counts as held: 100 W against reservations of whole MW.
# Replaying a B2 plan over a full year fell short by up to 5.7e-5 MW without its planning
# margin and by at most 3.9e-7 MW with it; a relative threshold would vanish for a tiny
# reservation, where that residue is as large.
TOL = 1e-4
# Slack, in MWh, that lets a plan sitting exactly on an energy band be followed despite
# rounding. It is worth at most TOL_SOC / (0.95 x hours) MW of reserve, about half of TOL at
# 1 hour, the shortest band a ReserveRule allows, so following such a plan never counts as
# reserve not held.
TOL_SOC = 5e-5


class Reservation(NamedTuple):
    """aFRR capacity held in one step, and the energy rule it keeps.

    up: MW of positive reserve held in the step, from 0 to the battery's rating and to the
        export limit, as the plans' constraints keep it.
    down: MW of negative reserve, from 0 to the battery's rating.
    rule: hours_up, hours_down and floor_mwh (0 drops the floor, as B2 plans).
    A reservation beyond those limits cannot be held: the layer still never forces the
    battery, and `check_feasible` counts the headroom it lacks.
    """
    up: float
    down: float
    rule: ReserveRule


def soc_next(soc: float, p_bat: float, dt: float, cfg: PlantConfig) -> float:
    """Battery energy balance. p_bat > 0 discharges."""
    p_ch, p_dis = max(-p_bat, 0.0), max(p_bat, 0.0)
    return soc + (cfg.eta_c * p_ch - p_dis / cfg.eta_d) * dt


def _band(mw: float, hours: float, floor_mwh: float) -> float:
    """MWh a reservation of `mw` keeps: `hours` of it, and at least the floor."""
    return max(mw * hours, floor_mwh)


def battery_interval(soc: float, dt: float, cfg: PlantConfig,
                     reserve: Reservation | None = None) -> tuple[float, float]:
    """Battery power interval (lo, hi) in MW, p_bat > 0 discharging, from rating and state of
    charge, and from the step's aFRR reservation where one is held.

    soc: stored energy at the start of the step, MWh. dt: step length, h. cfg: the plant.
    reserve: the step's reservation, or None. Positive reserve keeps rating headroom
    (p_bat <= bess_mw - up) and its band in store at the step's end (soc_min plus the band
    over eta_d); negative reserve keeps headroom the other way and the room its band needs
    below soc_max (the band times eta_c). A state of charge already past a band may stay or
    move back, never further, so 0 MW stays in the interval: restrict, never force.
    """
    lo = max(-cfg.bess_mw, -(cfg.soc_max - soc) / (cfg.eta_c * dt))
    hi = min(cfg.bess_mw, (soc - cfg.soc_min) * cfg.eta_d / dt)
    if hi < lo:
        lo = hi
    if reserve is None:
        return lo, hi
    rule = reserve.rule
    if reserve.up > 0:
        need = _band(reserve.up, rule.hours_up, rule.floor_mwh)        # MWh to deliver
        hi = min(hi, max(0.0, cfg.bess_mw - reserve.up),
                 max(0.0, (soc - cfg.soc_min - need / cfg.eta_d + TOL_SOC) * cfg.eta_d / dt))
    if reserve.down > 0:
        need = _band(reserve.down, rule.hours_down, rule.floor_mwh)    # MWh to absorb
        lo = max(lo, min(0.0, reserve.down - cfg.bess_mw),
                 min(0.0, -(cfg.soc_max - need * cfg.eta_c + TOL_SOC - soc) / (cfg.eta_c * dt)))
    return lo, hi


def _reserve_not_held(s0: float, s1: float, export: float, reserve: Reservation,
                      cfg: PlantConfig) -> tuple[float, float]:
    """MW of a step's reservation not held, up and down, each from 0 to its reservation.

    s0, s1: stored energy at the start and the end of the step, MWh. export: MW exported.
    The state of charge is linear within a step, so its lower end decides what positive
    reserve can deliver and its higher end what negative reserve can absorb. With A of the
    band's need MWh available, r MW holds r x A / need, so r x (1 - A / need) is not held;
    negative reserve is served by exporting less, so export short of it is not held either.
    For a fixed reservation these are the smallest shortfalls the plan's constraints allow, so
    the plan and the settlement charge the same. Below TOL a shortfall counts as 0.
    """
    up = down = 0.0
    rule = reserve.rule
    if reserve.up > 0:
        delivers = (min(s0, s1) - cfg.soc_min) * cfg.eta_d
        need = _band(reserve.up, rule.hours_up, rule.floor_mwh)
        up = reserve.up * min(1.0, max(0.0, 1.0 - delivers / need))
    if reserve.down > 0:
        absorbs = (cfg.soc_max - max(s0, s1)) / cfg.eta_c
        need = _band(reserve.down, rule.hours_down, rule.floor_mwh)
        down = max(reserve.down * min(1.0, max(0.0, 1.0 - absorbs / need)),
                   reserve.down - export)
    return (up if up >= TOL else 0.0), (down if down >= TOL else 0.0)


def dispatch_step(wind_avail: float, pv_avail: float, soc: float, p_bat_proposed: float,
                  curtail_frac: float, dt: float, cfg: PlantConfig,
                  export_cap: float | None = None,
                  reserve: Reservation | None = None) -> dict[str, float]:
    """Resolve one step into a feasible dispatch.

    `curtail_frac` in [0, 1] is the share of available generation the controller chooses to
    curtail (a decision, e.g. rational during negative prices). `export_cap` below the
    connection rating represents an active redispatch order.

    After the controller's choices, the point-of-interconnection limit is enforced by
    ADDITIONAL curtailment - the plant physically cannot export more than the connection
    allows, so this is the last resort, and the split between chosen and forced curtailment
    is reported because it is economically meaningful.

    `reserve`, the step's aFRR reservation or None, adds: the battery keeps its headroom and
    bands (`battery_interval`); it charges only from export above the negative reservation,
    as a negative call is served by exporting less and must never draw from the grid; export
    keeps the positive reservation's MW free below the connection, and the curtailment that
    alone forces is "reserve_curtail", apart from forced and chosen; and "afrr_deficit_up"
    and "afrr_deficit_down" give the MW not held (`_reserve_not_held`).
    """
    cap = cfg.conn_mw if export_cap is None else min(cfg.conn_mw, export_cap)
    # what a positive call would add to the export must fit under the connection as well
    limit = cap if reserve is None else max(0.0, cap - reserve.up)

    gen_avail = wind_avail + pv_avail
    chosen_curt = gen_avail * float(np.clip(curtail_frac, 0.0, 1.0))
    gen = gen_avail - chosen_curt

    lo, hi = battery_interval(soc, dt, cfg, reserve)
    if not cfg.grid_charging_allowed:
        # charging may not draw from the grid: the charge rate is limited by on-site surplus
        lo = max(lo, -gen)
    if reserve is not None and reserve.down > 0:
        # charge only from export above the negative reservation, never forced to discharge
        lo = max(lo, min(0.0, reserve.down - gen))
    p_bat = float(np.clip(p_bat_proposed, lo, hi))

    export = gen + p_bat
    forced_curt = 0.0
    if export > limit:
        # absorb the excess into the battery first if it can take it, then curtail
        room = max(0.0, -lo - max(-p_bat, 0.0)) if p_bat > -cfg.bess_mw else 0.0
        extra_charge = min(export - limit, room)
        p_bat -= extra_charge
        export = gen + p_bat
        if export > limit:
            forced_curt = export - limit
            gen -= forced_curt
            export = limit

    import_p = max(0.0, -export)          # only possible when grid charging is allowed
    export = max(0.0, export)
    p_ch, p_dis = max(-p_bat, 0.0), max(p_bat, 0.0)

    # Classify curtailment by CAUSE, not by who asked for it. Whatever the connection would
    # have forced anyway, given the battery power actually applied, is forced; only the rest
    # is a genuine economic choice. Labelling every requested curtailment as "chosen" counted
    # what the connection forced as a choice (test_curtailment_is_classified_by_cause), and
    # the split matters because ordered and self-chosen curtailment settle differently under
    # Redispatch 2.0.
    total_curt = gen_avail - gen
    forced_curt = min(total_curt, max(0.0, gen_avail + p_bat - cap))
    chosen_curt = total_curt - forced_curt
    out = {
        "export": export, "import": import_p, "gen": gen, "p_bat": p_bat,
        "p_ch": p_ch, "p_dis": p_dis,
        "chosen_curtail": chosen_curt, "forced_curtail": forced_curt,
        "throughput": (p_ch + p_dis) * dt,
        "soc": soc_next(soc, p_bat, dt, cfg),
    }
    if reserve is not None:
        # what positive reserve's headroom forces beyond the connection; 0 without it
        out["reserve_curtail"] = min(chosen_curt, max(0.0, gen_avail + p_bat - limit)
                                     - max(0.0, gen_avail + p_bat - cap))
        out["chosen_curtail"] = chosen_curt - out["reserve_curtail"]
        out["afrr_deficit_up"], out["afrr_deficit_down"] = _reserve_not_held(
            soc, out["soc"], export, reserve, cfg)
    return out


def _reservations(n: int, reserve_up, reserve_down,
                  rule: ReserveRule | None) -> tuple[np.ndarray, np.ndarray] | None:
    """`simulate`'s reservations as two arrays of n floats, or None where none are given.

    Raises ValueError unless reserve_up, reserve_down and rule come together, one finite
    value of at least 0 MW per step each.
    """
    given = [arg is not None for arg in (reserve_up, reserve_down, rule)]
    if not any(given):
        return None
    if not all(given):
        raise ValueError("reserve_up, reserve_down and rule go together: give all three or "
                         "none")
    up, down = (np.array(r, dtype=float) for r in (reserve_up, reserve_down))
    for name, r in (("reserve_up", up), ("reserve_down", down)):
        if r.shape != (n,) or not np.all(np.isfinite(r)) or np.any(r < 0):
            raise ValueError(f"{name} holds one finite reservation of at least 0 MW per step "
                             f"of the {n} steps")
    return up, down


def simulate(wind: np.ndarray, pv: np.ndarray, p_bat: np.ndarray, curtail: np.ndarray,
             dt: float, cfg: PlantConfig, soc0: float | None = None,
             export_cap: np.ndarray | None = None, reserve_up: np.ndarray | None = None,
             reserve_down: np.ndarray | None = None,
             rule: ReserveRule | None = None) -> dict[str, np.ndarray]:
    """Run proposed battery power and curtailment through the safety layer, step by step.

    wind, pv: available generation per step, MW. p_bat: proposed battery power per step, MW
    (> 0 discharges). curtail: share of generation to curtail per step, 0 to 1.
    dt: step length, h. cfg: the plant. soc0: stored energy at the start, MWh; None starts
    at cfg.soc_init. export_cap: export limit per step, MW, or None for the connection.
    reserve_up, reserve_down, rule: aFRR reservations per step, MW, e.g. np.full(n, 3.0), and
        the energy rule they keep; all three or none (`_reservations`).
    Returns one array per step for each key of `dispatch_step`'s result; with reservations
    also "afrr_up" and "afrr_down", the MW held, then "reserve_curtail", "afrr_deficit_up"
    and "afrr_deficit_down".
    """
    n = len(wind)
    keys = ("export", "import", "gen", "p_bat", "p_ch", "p_dis",
            "chosen_curtail", "forced_curtail", "throughput", "soc")
    out = {k: np.empty(n) for k in keys}
    reserved = _reservations(n, reserve_up, reserve_down, rule)
    if reserved is not None:
        out["afrr_up"], out["afrr_down"] = reserved
        keys += ("reserve_curtail", "afrr_deficit_up", "afrr_deficit_down")
        out.update({k: np.empty(n) for k in keys[-3:]})
    s = cfg.soc_init if soc0 is None else soc0
    for t in range(n):
        reserve = (None if reserved is None
                   else Reservation(float(reserved[0][t]), float(reserved[1][t]), rule))
        r = dispatch_step(float(wind[t]), float(pv[t]), s, float(p_bat[t]),
                          float(curtail[t]), dt, cfg,
                          None if export_cap is None else float(export_cap[t]), reserve)
        for k in keys:
            out[k][t] = r[k]
        s = r["soc"]
    return out


def check_feasible(res: dict[str, np.ndarray], cfg: PlantConfig,
                   tol: float = 1e-6) -> dict[str, int]:
    """Steps that break a hard limit, counted per limit; every count must be 0.

    res: `simulate`'s result. cfg: the plant it ran on. tol: slack in MW or MWh.
    Where res holds aFRR reservations ("afrr_up"), it also counts "reserve_power", steps
    whose battery power leaves less headroom than either reservation needs, and
    "reserve_connection", steps whose export leaves less than the positive reservation free
    below the connection. Reserve not held below its energy bands is not counted here: the
    settlement charges it.
    """
    counts = {
        "soc_low": int(np.sum(res["soc"] < cfg.soc_min - tol)),
        "soc_high": int(np.sum(res["soc"] > cfg.soc_max + tol)),
        "bess_rating": int(np.sum(np.abs(res["p_bat"]) > cfg.bess_mw + tol)),
        "export_cap": int(np.sum(res["export"] > cfg.conn_mw + tol)),
        "grid_charging": 0 if cfg.grid_charging_allowed
        else int(np.sum(res["import"] > tol)),
        "negative_curtailment": int(np.sum(res["chosen_curtail"] < -tol)
                                    + np.sum(res["forced_curtail"] < -tol)),
    }
    if "afrr_up" in res:
        up, down, p_bat = res["afrr_up"], res["afrr_down"], res["p_bat"]
        counts["reserve_power"] = int(np.sum((p_bat + up > cfg.bess_mw + tol)
                                             | (down - p_bat > cfg.bess_mw + tol)))
        counts["reserve_connection"] = int(np.sum(res["export"] + up > cfg.conn_mw + tol))
    return counts

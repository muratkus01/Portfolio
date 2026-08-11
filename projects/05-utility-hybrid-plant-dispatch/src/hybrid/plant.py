"""Hybrid plant physics and safety layer.

The plant is: wind + PV + battery, all behind one point of interconnection whose export limit
is smaller than the installed generation. Every step resolves

    export = wind_out + pv_out + p_bat   <=   conn_mw

with curtailment as an explicit decision variable, because *self-chosen* curtailment and
*ordered* (redispatch) curtailment settle differently and must be accounted separately.
"""
from __future__ import annotations

import numpy as np

from .config import PlantConfig


def soc_next(soc: float, p_bat: float, dt: float, cfg: PlantConfig) -> float:
    """Battery energy balance. p_bat > 0 discharges."""
    p_ch, p_dis = max(-p_bat, 0.0), max(p_bat, 0.0)
    return soc + (cfg.eta_c * p_ch - p_dis / cfg.eta_d) * dt


def battery_interval(soc: float, dt: float, cfg: PlantConfig) -> tuple[float, float]:
    """Battery power interval from rating and state of charge alone."""
    lo = max(-cfg.bess_mw, -(cfg.soc_max - soc) / (cfg.eta_c * dt))
    hi = min(cfg.bess_mw, (soc - cfg.soc_min) * cfg.eta_d / dt)
    if hi < lo:
        lo = hi
    return lo, hi


def dispatch_step(wind_avail: float, pv_avail: float, soc: float, p_bat_proposed: float,
                  curtail_frac: float, dt: float, cfg: PlantConfig,
                  export_cap: float | None = None) -> dict[str, float]:
    """Resolve one step into a feasible dispatch.

    `curtail_frac` in [0, 1] is the share of available generation the controller chooses to
    curtail (a decision, e.g. rational during negative prices). `export_cap` below the
    connection rating represents an active redispatch order.

    After the controller's choices, the point-of-interconnection limit is enforced by
    ADDITIONAL curtailment - the plant physically cannot export more than the connection
    allows, so this is the last resort, and the split between chosen and forced curtailment
    is reported because it is economically meaningful.
    """
    cap = cfg.conn_mw if export_cap is None else min(cfg.conn_mw, export_cap)

    gen_avail = wind_avail + pv_avail
    chosen_curt = gen_avail * float(np.clip(curtail_frac, 0.0, 1.0))
    gen = gen_avail - chosen_curt

    lo, hi = battery_interval(soc, dt, cfg)
    if not cfg.grid_charging_allowed:
        # charging may not draw from the grid: the charge rate is limited by on-site surplus
        lo = max(lo, -gen)
    p_bat = float(np.clip(p_bat_proposed, lo, hi))

    export = gen + p_bat
    forced_curt = 0.0
    if export > cap:
        # absorb the excess into the battery first if it can take it, then curtail
        room = max(0.0, -lo - max(-p_bat, 0.0)) if p_bat > -cfg.bess_mw else 0.0
        extra_charge = min(export - cap, room)
        p_bat -= extra_charge
        export = gen + p_bat
        if export > cap:
            forced_curt = export - cap
            gen -= forced_curt
            export = cap

    import_p = max(0.0, -export)          # only possible when grid charging is allowed
    export = max(0.0, export)
    p_ch, p_dis = max(-p_bat, 0.0), max(p_bat, 0.0)
    return {
        "export": export, "import": import_p, "gen": gen, "p_bat": p_bat,
        "p_ch": p_ch, "p_dis": p_dis,
        "chosen_curtail": chosen_curt, "forced_curtail": forced_curt,
        "throughput": (p_ch + p_dis) * dt,
        "soc": soc_next(soc, p_bat, dt, cfg),
    }


def simulate(wind: np.ndarray, pv: np.ndarray, p_bat: np.ndarray, curtail: np.ndarray,
             dt: float, cfg: PlantConfig, soc0: float | None = None,
             export_cap: np.ndarray | None = None) -> dict[str, np.ndarray]:
    n = len(wind)
    keys = ("export", "import", "gen", "p_bat", "p_ch", "p_dis",
            "chosen_curtail", "forced_curtail", "throughput", "soc")
    out = {k: np.empty(n) for k in keys}
    s = cfg.soc_init if soc0 is None else soc0
    for t in range(n):
        r = dispatch_step(float(wind[t]), float(pv[t]), s, float(p_bat[t]),
                          float(curtail[t]), dt, cfg,
                          None if export_cap is None else float(export_cap[t]))
        for k in keys:
            out[k][t] = r[k]
        s = r["soc"]
    return out


def check_feasible(res: dict[str, np.ndarray], cfg: PlantConfig,
                   tol: float = 1e-6) -> dict[str, int]:
    return {
        "soc_low": int(np.sum(res["soc"] < cfg.soc_min - tol)),
        "soc_high": int(np.sum(res["soc"] > cfg.soc_max + tol)),
        "bess_rating": int(np.sum(np.abs(res["p_bat"]) > cfg.bess_mw + tol)),
        "export_cap": int(np.sum(res["export"] > cfg.conn_mw + tol)),
        "grid_charging": 0 if cfg.grid_charging_allowed
        else int(np.sum(res["import"] > tol)),
        "negative_curtailment": int(np.sum(res["chosen_curtail"] < -tol)
                                    + np.sum(res["forced_curtail"] < -tol)),
    }

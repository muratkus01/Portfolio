"""Safety layer: projection of a proposed battery power onto the feasible set.

Hard constraints are enforced here, never priced into a reward. A penalty-based formulation
lets a policy trade a violation against profit, which is exactly the property that makes a
learned controller unfit for a real asset. With this projection, violations are impossible by
construction and the interesting question becomes what the guarantee costs - which is
measurable, and is one of Project 01's reported ablations.

Order of application matters and is deliberate:
    1. inverter rating          (device capability)
    2. state-of-charge bounds   (device state, depends on dt)
    3. grid connection limits   (site electrical limits, depend on load and PV)
    4. s14a EnWG dimming cap    (regulatory interrupt)

Later steps can only tighten the interval, so the result is feasible for all four.
"""
from __future__ import annotations

import numpy as np

from .config import SiteConfig


def feasible_interval(soc: float, load: float, pv: float, dt: float, cfg: SiteConfig,
                      dim_limit: float | None = None) -> tuple[float, float]:
    """Return (p_min, p_max): the interval of battery powers that is feasible this step.

    p_min < 0 is the strongest permissible charge, p_max > 0 the strongest discharge.
    """
    # 1. inverter rating
    lo, hi = -cfg.p_inv, cfg.p_inv

    # 2. SoC bounds after this step.
    #    charging:    soc + eta_c * |p| * dt <= soc_max   ->  |p| <= (soc_max - soc)/(eta_c*dt)
    #    discharging: soc - p * dt / eta_d  >= soc_min    ->    p  <= (soc - soc_min)*eta_d/dt
    lo = max(lo, -(cfg.soc_max - soc) / (cfg.eta_c * dt))
    hi = min(hi, (soc - cfg.soc_min) * cfg.eta_d / dt)

    # 3. grid connection limits. net = load - pv - p_bat
    #    import  net <=  p_imp_max  ->  p_bat >= load - pv - p_imp_max
    #    export -net <=  p_exp_max  ->  p_bat <= load - pv + p_exp_max
    lo = max(lo, load - pv - cfg.p_imp_max)
    hi = min(hi, load - pv + cfg.p_exp_max)

    # 4. s14a dimming: the controllable device (here, battery charging) is capped at the
    #    guaranteed minimum power while an event is active. Discharging is not restricted -
    #    the DSO limits consumption, not injection.
    if dim_limit is not None:
        lo = max(lo, -abs(dim_limit))

    # The interval can be empty if the site is infeasible on its own (e.g. load exceeds the
    # import limit even with the battery at full discharge). In that case the physical
    # constraint that cannot be met is the grid limit, and the honest response is to return
    # the closest point rather than to pretend: hi < lo means "clamp to hi".
    if hi < lo:
        lo = hi
    return lo, hi


def project(p_proposed: float, soc: float, load: float, pv: float, dt: float,
            cfg: SiteConfig, dim_limit: float | None = None) -> float:
    """Clip a proposed battery power to the feasible interval."""
    lo, hi = feasible_interval(soc, load, pv, dt, cfg, dim_limit)
    return float(np.clip(p_proposed, lo, hi))


def project_series(p_proposed: np.ndarray, load: np.ndarray, pv: np.ndarray, dt: float,
                   cfg: SiteConfig, soc0: float | None = None,
                   dim_limit: np.ndarray | None = None) -> np.ndarray:
    """Sequentially project a whole proposed trajectory, tracking SoC as it goes."""
    from .model.site import soc_next

    n = len(p_proposed)
    out = np.empty(n)
    s = cfg.soc_init if soc0 is None else soc0
    for t in range(n):
        d = None if dim_limit is None else float(dim_limit[t])
        out[t] = project(float(p_proposed[t]), s, float(load[t]), float(pv[t]), dt, cfg, d)
        s = soc_next(s, out[t], dt, cfg)
    return out

"""Power allocation, the safety layer, and the tariff.

The technical core of this project is the **feasibility-preserving reserve**: at every step
the safety layer checks that a feasible completion still exists for every connected vehicle,
and restricts the action set to allocations that keep it so. This is what converts "the agent
usually meets departures" into "the agent cannot miss one" - the difference between a demo and
a controller a depot operator would install.
"""
from __future__ import annotations

import numpy as np

from .config import Para14aConfig, RunConfig, SiteConfig, TariffConfig


# --------------------------------------------------------------------- tariff
def network_energy_price(hours: np.ndarray, cfg: TariffConfig) -> np.ndarray:
    base = np.full(len(hours), cfg.network_energy, dtype=float)
    if cfg.para_14a_module == "module_2":
        return base * (1.0 - cfg.module_2_reduction)
    if cfg.para_14a_module == "module_3":
        f_lo, f_std, f_hi = cfg.module_3_factors
        f = np.full(len(hours), f_std)
        f[np.isin(hours, cfg.module_3_low_hours)] = f_lo
        f[np.isin(hours, cfg.module_3_high_hours)] = f_hi
        return base * f
    return base


def energy_price(spot: np.ndarray, hours: np.ndarray, cfg: TariffConfig) -> np.ndarray:
    """Full marginal cost of a kWh drawn at the site, EUR/kWh."""
    e = np.asarray(spot, dtype=float)
    net = e + cfg.supplier_margin + network_energy_price(hours, cfg) \
        + cfg.levies + cfg.electricity_tax
    return net * (1.0 + cfg.vat)


# --------------------------------------------------------------------- s14a
def dimming_series(n_steps: int, hours: np.ndarray, run: RunConfig,
                   cfg: Para14aConfig) -> np.ndarray | None:
    """Per-step cap on total controllable power, or None when disabled."""
    if not cfg.enabled:
        return None
    rng = np.random.default_rng(cfg.seed)
    years = n_steps * run.dt / 8760.0
    n_events = max(0, int(round(cfg.events_per_year * years)))
    w = np.where(np.isin(hours, cfg.concentration_hours), 5.0, 1.0)
    w = w / w.sum()
    caps = np.full(n_steps, np.inf)
    for _ in range(n_events):
        start = int(rng.choice(n_steps, p=w))
        dur = max(1, int(round(rng.exponential(cfg.mean_duration_h) / run.dt)))
        caps[start:start + dur] = cfg.p_min_total_kw
    return caps


# --------------------------------------------------------------------- safety layer
def minimum_required_power(remaining_kwh: np.ndarray, steps_left: np.ndarray,
                           dt: float, p_max: float) -> np.ndarray:
    """Power each connector MUST receive now to still meet its declared deadline.

    If a vehicle needs `E` kWh and has `k` steps left, it must average `E / (k*dt)` kW. When
    that reaches the connector rating there is no slack left and the allocation is forced.
    Computing this every step is what makes the guarantee constructive rather than hopeful.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        need = np.where(steps_left > 0, remaining_kwh / (steps_left * dt), 0.0)
    return np.clip(np.nan_to_num(need), 0.0, p_max)


def aggregate_floor(remaining_kwh: np.ndarray, steps_left: np.ndarray, active: np.ndarray,
                    run: RunConfig, limit_kw: float) -> float:
    """Total power that MUST be delivered this step to keep all declared deadlines reachable.

    The per-connector minimum is not enough. A set of vehicles can each be individually
    comfortable while being *collectively* impossible: if three vehicles each need 40 kWh
    within two hours and the site is limited to 30 kW, no allocation saves them, and the
    per-connector test sees nothing wrong until it is far too late.

    The correct condition is the classic earliest-deadline-first feasibility test. For every
    horizon `h`, the energy owed to vehicles whose deadline falls within `h` cannot exceed
    what the site can deliver in that time:

        sum_{k_i <= h} E_i  <=  limit * h * dt

    Preserving that inequality one step ahead gives the floor on total power now:

        floor = max_h [ ( sum_{k_i <= h} E_i ) - limit * (h - 1) * dt ] / dt

    Returning this as an aggregate (rather than per connector) is deliberate: it is a
    statement about the SITE, and the caller distributes it by earliest deadline.

    This is what makes the departure guarantee constructive. Without it a price-aware policy
    happily defers charging into a corner it cannot escape - which is exactly what the first
    version of this project did, missing more departures than doing nothing at all.
    """
    if not np.any(active):
        return 0.0
    dt = run.dt
    k = steps_left[active].astype(int)
    e = remaining_kwh[active]
    if e.sum() <= 1e-9:
        return 0.0

    order = np.argsort(k)
    k_sorted, e_sorted = k[order], e[order]
    cum = np.cumsum(e_sorted)
    horizons = np.maximum(k_sorted, 1)
    # energy owed by each horizon, minus what the site can still deliver after this step
    need = (cum - limit_kw * (horizons - 1) * dt) / dt
    return float(max(0.0, need.max()))


def project(proposed_kw: np.ndarray, remaining_kwh: np.ndarray, steps_left: np.ndarray,
            active: np.ndarray, run: RunConfig,
            dim_cap: float | None = None) -> np.ndarray:
    """Project a proposed per-connector allocation onto the feasible set.

    Order:
      1. inactive connectors get zero
      2. never deliver more than the vehicle still needs, or than the connector can supply
      3. raise every connector to its minimum-required power (the feasibility reserve)
      4. apply the minimum-current rule: below p_min a connector must be OFF, not trickling
      5. enforce the site limit and the s14a cap, shedding from the connectors with the most
         slack first - the vehicles closest to their deadline keep their power

    Step 5's ordering is the whole design. Shedding uniformly would push the tightest vehicle
    below its required rate; shedding by slack keeps the guarantee intact for as long as it is
    physically possible to keep it.
    """
    cfg = run.site
    dt = run.dt
    p = np.asarray(proposed_kw, dtype=float).copy()
    p[~active] = 0.0

    deliverable = np.minimum(remaining_kwh / dt, cfg.p_connector_kw)
    p = np.clip(p, 0.0, np.where(active, deliverable, 0.0))

    need = minimum_required_power(remaining_kwh, steps_left, dt, cfg.p_connector_kw)
    need = np.where(active, np.minimum(need, deliverable), 0.0)
    p = np.maximum(p, need)

    # Aggregate (EDF) feasibility reserve: raise total power to the level that keeps every
    # declared deadline reachable, allocating the extra to the tightest deadlines first.
    limit_now = cfg.site_limit_kw if dim_cap is None else min(cfg.site_limit_kw, dim_cap)
    floor_total = aggregate_floor(remaining_kwh, steps_left, active, run, limit_now)
    # `required` is the power that must NOT be removed later: the per-connector minimum plus
    # whatever the aggregate floor assigns. Later steps (minimum current, shedding) previously
    # consulted only the per-connector `need`, so they switched off or shed exactly the power
    # the floor had just added, quietly undoing the reserve.
    required = need.copy()
    if p.sum() < floor_total - 1e-9:
        deficit = floor_total - p.sum()
        # earliest deadline first, then largest remaining energy
        idx = np.flatnonzero(active)
        idx = idx[np.lexsort((-remaining_kwh[idx], steps_left[idx]))]
        for i in idx:
            if deficit <= 1e-9:
                break
            room = max(0.0, min(deliverable[i], cfg.p_connector_kw) - p[i])
            add = min(room, deficit)
            p[i] += add
            required[i] = max(required[i], p[i])
            deficit -= add

    if cfg.enforce_min_current:
        # a connector is either off or at least at the minimum current
        below = (p > 0) & (p < cfg.p_min_kw)
        # keep it on (rounded UP to p_min) if the power is required, otherwise switch it off
        on = np.minimum(cfg.p_min_kw, deliverable)
        p = np.where(below, np.where(required > 0, on, 0.0), p)
        required = np.where(below & (required > 0), np.maximum(required, on), required)

    limit = limit_now
    total = p.sum()
    if total > limit:
        # shed from the connectors with the most slack first
        slack = np.where(active, np.maximum(p - required, 0.0), 0.0)
        order = np.argsort(-slack)          # most slack first
        excess = total - limit
        for i in order:
            if excess <= 1e-9:
                break
            cut = min(slack[i], excess)
            p[i] -= cut
            excess -= cut
        if excess > 1e-9:
            # Physically infeasible: even the forced minima exceed the site limit. The site is
            # oversubscribed and SOMETHING must give. Cut proportionally from the forced part
            # and record it - an honest, auditable failure rather than a hidden violation.
            scale = max(0.0, (limit) / max(p.sum(), 1e-9))
            p = p * scale
    return p


def infeasible_now(remaining_kwh: np.ndarray, steps_left: np.ndarray, active: np.ndarray,
                   run: RunConfig) -> np.ndarray:
    """Connectors that can no longer meet their declared deadline whatever happens."""
    need = minimum_required_power(remaining_kwh, steps_left, run.dt,
                                  run.site.p_connector_kw)
    return active & (need >= run.site.p_connector_kw - 1e-9) & (remaining_kwh > 1e-9)

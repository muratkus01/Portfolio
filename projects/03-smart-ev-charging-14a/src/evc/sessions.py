"""EV session generation and the site state machine.

Sessions are synthesised from archetype parameters. The real project fits session STRUCTURE
(energy per session, dwell-time shape, arrival clustering) from ACN-Data and ElaadNL and
re-weights the arrival/departure marginals with German mobility statistics; that substitution
and its effect are a reported limitation, not a hidden assumption. The generator here has the
same interface, so swapping in fitted distributions changes only this module.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ArchetypeConfig, RunConfig


@dataclass
class Session:
    connector: int
    arrive: int            # step index
    depart_true: int       # when the vehicle actually leaves
    depart_declared: int   # what the driver said - the deadline the site must honour
    energy_kwh: float      # energy required by departure
    p_max_kw: float
    # True when the vehicle's natural departure lies beyond the simulated window. It is still
    # simulated (it occupies a connector and draws power) but its outcome is never observed,
    # so it must not be scored as a missed departure. See `generate`.
    censored: bool = False

    def active(self, t: int) -> bool:
        return self.arrive <= t < self.depart_true


def generate(run: RunConfig, arch: ArchetypeConfig | None = None) -> list[Session]:
    """Synthesise sessions over the run's horizon, respecting connector availability."""
    arch = arch or run.archetype
    rng = np.random.default_rng(run.seed)
    spd = run.steps_per_day
    n_steps = run.days * spd
    sessions: list[Session] = []
    # per-connector next-free step, so two sessions never overlap on one connector
    free_at = np.zeros(run.site.n_connectors, dtype=int)

    for day in range(run.days):
        n_arr = rng.poisson(arch.arrivals_per_day)
        for _ in range(n_arr):
            hour = rng.normal(arch.arrival_hour_mean, arch.arrival_hour_sd)
            arrive = day * spd + int(np.clip(hour / run.dt, 0, spd - 1))
            if arrive >= n_steps:
                continue
            free = np.flatnonzero(free_at <= arrive)
            if len(free) == 0:
                continue                      # site full: the vehicle is turned away
            conn = int(free[0])

            dwell = max(run.dt, rng.normal(arch.dwell_hours_mean, arch.dwell_hours_sd))
            # Right-censoring. The departure is drawn from the vehicle's natural dwell and is
            # NOT truncated at the end of the simulated window. Truncating it (the first
            # version did) squeezed a depot car arriving at 16:30 on the final day into the
            # last seven hours, so every such car demanded full power at once and the site
            # "missed" departures that no controller could have met. Those misses were an
            # artefact of where the simulation stops, and identical across every controller.
            natural_depart = arrive + max(1, int(dwell / run.dt))
            depart_true = min(n_steps, natural_depart)
            # declared departure is EARLIER than the truth (drivers are pessimistic)
            bias = arch.declaration_bias_h + rng.normal(0, arch.declaration_noise_h)
            depart_decl = int(np.clip(natural_depart - bias / run.dt, arrive + 1,
                                      natural_depart))

            energy = float(np.clip(rng.normal(arch.energy_kwh_mean, arch.energy_kwh_sd),
                                   2.0, 100.0))
            # never require more than is physically deliverable by the declared departure
            max_deliverable = (depart_decl - arrive) * run.dt * run.site.p_connector_kw
            energy = min(energy, max_deliverable)

            sessions.append(Session(conn, arrive, depart_true, depart_decl, energy,
                                    run.site.p_connector_kw,
                                    censored=natural_depart > n_steps))
            free_at[conn] = depart_true
    return sessions


def occupancy_matrix(sessions: list[Session], n_steps: int,
                     n_connectors: int) -> np.ndarray:
    """Boolean [step, connector]: is a vehicle plugged in and still needing energy?"""
    occ = np.zeros((n_steps, n_connectors), dtype=bool)
    for s in sessions:
        occ[s.arrive:s.depart_true, s.connector] = True
    return occ


def energy_requirement(sessions: list[Session], n_steps: int,
                       n_connectors: int) -> np.ndarray:
    """Required energy per (step, connector), constant while a session is active."""
    req = np.zeros((n_steps, n_connectors))
    for s in sessions:
        req[s.arrive:s.depart_true, s.connector] = s.energy_kwh
    return req


def deadline_matrix(sessions: list[Session], n_steps: int,
                    n_connectors: int) -> np.ndarray:
    """Steps remaining until the DECLARED departure. -1 where no session is active."""
    dl = np.full((n_steps, n_connectors), -1, dtype=int)
    for s in sessions:
        for t in range(s.arrive, s.depart_true):
            dl[t, s.connector] = max(0, s.depart_declared - t)
    return dl


def summarise(sessions: list[Session], run: RunConfig) -> str:
    if not sessions:
        return "no sessions"
    e = np.array([s.energy_kwh for s in sessions])
    dwell = np.array([(s.depart_true - s.arrive) * run.dt for s in sessions])
    slack = np.array([
        (s.depart_declared - s.arrive) * run.dt - s.energy_kwh / s.p_max_kw
        for s in sessions])
    # Utilisation against the DELIVERABLE window, not against the clock. A site can have
    # ample daily energy capacity and still be infeasible if all the dwell time is at night
    # and the site limit is low. This ratio is the single number that says whether the
    # departure guarantee is achievable at all, and it belongs in the run header rather than
    # being discovered later from a non-zero miss count.
    window_h = float(np.median([(s.depart_declared - s.arrive) * run.dt for s in sessions]))
    deliverable_per_day = run.site.site_limit_kw * window_h
    demand_per_day = e.sum() / run.days
    util = demand_per_day / max(deliverable_per_day, 1e-9)
    verdict = "feasible" if util < 0.85 else ("TIGHT" if util < 1.0 else "OVERSUBSCRIBED")
    return (f"{len(sessions)} sessions over {run.days} days | "
            f"energy {e.mean():.1f} +/- {e.std():.1f} kWh (total {e.sum():,.0f}) | "
            f"dwell {dwell.mean():.1f} h | "
            f"slack {slack.mean():.1f} h (min {slack.min():.1f})\n"
            f"  uncontrolled peak {run.site.n_connectors * run.site.p_connector_kw:.0f} kW "
            f"vs site limit {run.site.site_limit_kw:.0f} kW | "
            f"demand {demand_per_day:,.0f} kWh/day vs deliverable "
            f"{deliverable_per_day:,.0f} kWh in the {window_h:.1f} h charging window "
            f"-> utilisation {util:.0%} ({verdict})")

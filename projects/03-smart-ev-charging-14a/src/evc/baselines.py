"""Benchmark ladder for the charging hub.

B0  uncontrolled       charge at full power on arrival - the status quo at most sites
B1  equal share        static load management: split the available capacity evenly
B2  perfect foresight  price-aware allocation knowing every arrival and true departure
B3  rolling MPC        price-aware, greedy over the horizon, on declared departures only

All rungs pass through the same safety layer, so every one of them delivers zero missed
declared departures whenever that is physically possible. What differs is the COST of doing so.
"""
from __future__ import annotations

import numpy as np

from .allocate import energy_price, infeasible_now, project
from .config import RunConfig
from .sessions import Session


class SiteState:
    """Tracks per-connector remaining energy and time to the declared deadline."""

    def __init__(self, sessions: list[Session], n_steps: int, run: RunConfig):
        self.run = run
        self.n = run.site.n_connectors
        self.n_steps = n_steps
        self.remaining = np.zeros(self.n)
        self.steps_left = np.zeros(self.n, dtype=int)
        self.active = np.zeros(self.n, dtype=bool)
        self.by_start: dict[int, list[Session]] = {}
        self.by_end: dict[int, list[Session]] = {}
        for s in sessions:
            self.by_start.setdefault(s.arrive, []).append(s)
            self.by_end.setdefault(s.depart_true, []).append(s)
        self.delivered: dict[int, float] = {}
        self.required: dict[int, float] = {}
        self._live: dict[int, Session] = {}
        self.missed = 0
        self.shortfall_kwh = 0.0

    def begin_step(self, t: int) -> None:
        for s in self.by_start.get(t, []):
            self.remaining[s.connector] = s.energy_kwh
            self.active[s.connector] = True
            self._live[s.connector] = s
            self.required[id(s)] = s.energy_kwh
            self.delivered[id(s)] = 0.0
        for c in range(self.n):
            s = self._live.get(c)
            self.steps_left[c] = max(0, s.depart_declared - t) if s else 0

    def apply(self, p: np.ndarray, t: int) -> None:
        e = p * self.run.dt
        for c in range(self.n):
            s = self._live.get(c)
            if s is None:
                continue
            self.delivered[id(s)] += e[c]
            self.remaining[c] = max(0.0, self.remaining[c] - e[c])

    def end_step(self, t: int) -> None:
        for s in self.by_end.get(t + 1, []):
            c = s.connector
            short = max(0.0, self.required[id(s)] - self.delivered[id(s)])
            if short > 1e-6:
                self.missed += 1
                self.shortfall_kwh += short
            self.active[c] = False
            self.remaining[c] = 0.0
            self._live.pop(c, None)


def _run(sessions: list[Session], n_steps: int, run: RunConfig,
         propose, price: np.ndarray, dim: np.ndarray | None) -> dict:
    st = SiteState(sessions, n_steps, run)
    P = np.zeros((n_steps, run.site.n_connectors))
    infeas = 0
    for t in range(n_steps):
        st.begin_step(t)
        infeas += int(infeasible_now(st.remaining, st.steps_left, st.active, run).sum())
        prop = propose(t, st)
        cap = None if dim is None or not np.isfinite(dim[t]) else float(dim[t])
        p = project(prop, st.remaining, st.steps_left, st.active, run, cap)
        P[t] = p
        st.apply(p, t)
        st.end_step(t)
    total = P.sum(axis=1)
    return {
        "power": P, "site_kw": total, "peak_kw": float(total.max()),
        "energy_kwh": float(total.sum() * run.dt),
        "missed_departures": st.missed, "shortfall_kwh": st.shortfall_kwh,
        "forced_infeasible_steps": infeas,
        "switching": int(np.sum(np.abs(np.diff((P > 0).astype(int), axis=0)))),
    }


# ------------------------------------------------------------------ policies
def b0_uncontrolled(sessions, n_steps, run, price, dim=None) -> dict:
    def propose(t, st):
        return np.where(st.active, run.site.p_connector_kw, 0.0)
    return _run(sessions, n_steps, run, propose, price, dim)


def b1_equal_share(sessions, n_steps, run, price, dim=None) -> dict:
    def propose(t, st):
        k = max(1, int(st.active.sum()))
        share = min(run.site.p_connector_kw, run.site.site_limit_kw / k)
        return np.where(st.active, share, 0.0)
    return _run(sessions, n_steps, run, propose, price, dim)


def b3_price_greedy(sessions, n_steps, run, price, dim=None,
                    horizon: int | None = None) -> dict:
    """Charge hard when the price is in the cheapest part of the remaining window.

    A greedy stand-in for the full MILP: for each connector, compare the current price with
    the distribution of prices remaining before its declared departure, and charge at full
    power when the current step ranks among the cheapest it will still see. The safety layer
    supplies the deadline guarantee, so the policy only has to decide *when*, never *whether*.

    This is deliberately not the strongest possible baseline - the peak-tracking MILP is - but
    it is honest about being a stand-in, and it already captures most of the price-shifting
    value.
    """
    h = horizon or run.horizon_steps

    def propose(t, st):
        out = np.zeros(run.site.n_connectors)
        for c in range(run.site.n_connectors):
            if not st.active[c]:
                continue
            k = max(1, int(st.steps_left[c]))
            window = price[t:min(t + min(k, h), n_steps)]
            if len(window) == 0:
                out[c] = run.site.p_connector_kw
                continue
            # how many of the remaining steps do we actually need at full power?
            need_steps = int(np.ceil(st.remaining[c] / (run.site.p_connector_kw * run.dt)))
            thresh = np.partition(window, min(need_steps, len(window) - 1))[
                min(need_steps, len(window) - 1)]
            out[c] = run.site.p_connector_kw if price[t] <= thresh else 0.0
        return out
    return _run(sessions, n_steps, run, propose, price, dim)


# ------------------------------------------------------------------ costing
def cost(res: dict, spot: np.ndarray, hours: np.ndarray, run: RunConfig) -> dict[str, float]:
    """Site cost: energy + network + peak, less THG-Quote revenue."""
    cfg = run.tariff
    p_kw = res["site_kw"]
    price = energy_price(spot, hours, cfg)
    energy_cost = float(np.sum(p_kw * price) * run.dt)
    years = len(p_kw) * run.dt / 8760.0
    peak_cost = res["peak_kw"] * cfg.peak_charge_eur_kw_year * years
    thg = res["energy_kwh"] * cfg.thg_eur_kwh
    module_1 = cfg.module_1_annual_reduction * years if cfg.para_14a_module == "module_1" else 0.0
    net = energy_cost + peak_cost - thg - module_1
    return {"energy_cost": energy_cost, "peak_cost": peak_cost, "thg_revenue": thg,
            "module_1_credit": module_1, "net_cost": net}


def fairness(res: dict) -> float:
    """Jain index over per-connector delivered energy. 1 = perfectly equal."""
    e = res["power"].sum(axis=0)
    e = e[e > 0]
    if len(e) == 0:
        return 1.0
    return float(e.sum() ** 2 / (len(e) * np.sum(e ** 2)))

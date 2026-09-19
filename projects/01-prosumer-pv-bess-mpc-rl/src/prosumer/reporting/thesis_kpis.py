"""The thesis's techno-economic KPIs, computed for any trajectory of this package.

`settlement.settle` is the package's single cost function and what every controller is
optimised and ranked by. This module is a REPORT on top of it: it reproduces the yearly
summary table of the published thesis (optimisation profit, net bill, cycles,
self-consumption, self-sufficiency, ...) so that new controllers can be placed in the
thesis's own table next to the published scenarios.

Every energy and money term is multiplied by `dt`, so the same code serves hourly and
quarter-hourly data. At dt = 1 h it reproduces the published numbers (see
tests/test_thesis_reproduction.py).

saving_basis
    "load_met"   : saving = EP_buy * (PV direct use + battery to load)
                   (thesis definition in the optimized scenario)
    "dc_to_load" : saving = EP_buy * (battery to load)
                   (thesis definition in the rule-based and PV-only scenarios)
The two definitions make the published 'optimisation profit' of the optimized and the
rule-based scenario not directly comparable. New results use "load_met" throughout.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SiteConfig
from ..thesis import THESIS_DOD, THESIS_PV_KWP, CostProfile

SAVING_BASES = ("load_met", "dc_to_load")


def flows(res: dict, load: np.ndarray, pv: np.ndarray) -> dict[str, np.ndarray]:
    """Decompose a trajectory into the thesis's power flows (kW)."""
    ch, dis = np.asarray(res["p_ch"]), np.asarray(res["p_dis"])
    ch_pv = np.minimum(np.maximum(0.0, pv - load), ch)
    dc_load = np.where(load > pv, np.minimum(load - pv, dis), 0.0)
    pv_site = np.minimum(pv, load + ch)
    load_met = dc_load + np.minimum(pv, load)
    return {
        "ch_from_pv": ch_pv, "ch_from_grid": np.maximum(0.0, ch - ch_pv),
        "dc_to_load": dc_load, "dc_to_grid": dis - dc_load,
        "pv_on_site": pv_site, "pv_to_grid": np.maximum(0.0, pv - pv_site),
        "load_met": load_met, "load_from_grid": np.maximum(0.0, load - load_met),
    }


def thesis_summary(res: dict, load, pv, ep_buy, ep_sell, index: pd.DatetimeIndex, dt: float,
                   site: SiteConfig, cost: CostProfile, saving_basis: str = "load_met",
                   pv_kwp: float = THESIS_PV_KWP, has_battery: bool = True
                   ) -> dict[str, float]:
    """The thesis 'Yearly Summary' row for one trajectory. Money in EUR, energy in kWh."""
    if saving_basis not in SAVING_BASES:
        raise ValueError(f"saving_basis must be one of {SAVING_BASES}")
    load, pv, ep_buy, ep_sell = (np.asarray(x, float) for x in (load, pv, ep_buy, ep_sell))
    f = flows(res, load, pv)

    saved = f["load_met"] if saving_basis == "load_met" else f["dc_to_load"]
    saving = np.sum(ep_buy * saved) * dt
    earn_dc = np.sum(ep_sell * f["dc_to_grid"]) * dt
    earn_pv = np.sum(ep_sell * f["pv_to_grid"]) * dt
    opp_cost = np.sum(ep_sell * f["ch_from_pv"]) * dt
    cost_ch = np.sum(ep_buy * f["ch_from_grid"]) * dt
    cost_load = np.sum(ep_buy * f["load_from_grid"]) * dt
    energy_cost = cost_ch + cost_load
    earnings = earn_dc + earn_pv

    fixed = cost.annual_fixed
    capex = cost.capex(site, pv_kwp)
    profit = saving + earnings - opp_cost - energy_cost - fixed
    e_bess = site.e_bess if has_battery else 0.0
    cycles = np.sum(res["p_dis"]) * dt / (site.e_bess * THESIS_DOD) if has_battery else 0.0
    pv_e, load_e = pv.sum() * dt, load.sum() * dt

    return {
        "PV [kW]": pv_kwp, "Battery [kWh]": e_bess, "Inverter [kW]": site.p_inv,
        "CAPEX [€]": capex,
        "Self-Consumption [%]": 100 * f["pv_on_site"].sum() * dt / pv_e if pv_e else 0.0,
        "Self-Sufficiency [%]": 100 * f["load_met"].sum() * dt / load_e if load_e else 0.0,
        "Earning for Discharge to Grid": earn_dc,
        "Earning for PV Feed-in to Grid": earn_pv,
        "Opportunity Cost for Not PV Feed-in to Grid ": opp_cost,
        "Saving for Avoided Grid Import": saving,
        "Cost for Covering Load from Grid": cost_load,
        "Cost for Charging Battery from Grid": cost_ch,
        "Total Earnings": earnings,
        "Energy Cost": energy_cost,
        "Fixed Annual Cost [€]": fixed,
        "Net Bill [€]": earnings - energy_cost - fixed,
        "Optimization Profit": profit,
        "Cycles": cycles,
        "Grid Import [kWh]": np.sum(res["p_imp"]) * dt,
        "Grid Export [kWh]": np.sum(res["p_exp"]) * dt,
        "Opt. Profit / kWh Battery [€/kWh]": profit / e_bess if e_bess else 0.0,
        "Opt. Profit / kWp PV [€/kW]": profit / pv_kwp,
        "Opt. Profit / kW Inverter [€/kW]": profit / site.p_inv,
        "Opt. Profit / CAPEX": profit / capex,
        "Cycles / CAPEX [cycle/€]": cycles / capex,
    }

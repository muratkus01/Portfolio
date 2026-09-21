"""
run_overnight_simulations.py
============================
Batch parameter sweep across all 6 portfolio projects.

Usage
-----
  python scripts/run_overnight_simulations.py --smoke-test
  python scripts/run_overnight_simulations.py --full
  python scripts/run_overnight_simulations.py --full --workers 16
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import itertools
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "simulations" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

CHECKPOINT_FILE = RESULTS / "checkpoint.json"
AUDIT_FILE      = RESULTS / "audit_report.json"
RUN_LOG         = ROOT / "simulations" / "run.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(RUN_LOG), encoding="utf-8"),
    ],
)
log = logging.getLogger("sim")

DATAKIT = ROOT / "datakit" / "processed"


def _load_price(year: int) -> np.ndarray:
    df = pd.read_parquet(DATAKIT / f"price_de_lu_{year}.parquet")
    col = [c for c in df.columns if "price" in c.lower() or "da" in c.lower() or "eur" in c.lower()][0]
    return df[col].values.astype(float)


def _load_pv_profile(year: int, kwp: float = 1.0) -> np.ndarray:
    df = pd.read_parquet(DATAKIT / f"public_power_de_{year}.parquet")
    col = [c for c in df.columns if "solar" in c.lower() or "pv" in c.lower()][0]
    raw = df[col].values.astype(float)
    raw = raw / raw.max() if raw.max() > 0 else raw
    return raw * kwp


def _load_wind_profile(year: int, mw: float = 1.0) -> np.ndarray:
    df = pd.read_parquet(DATAKIT / f"public_power_de_{year}.parquet")
    col = [c for c in df.columns if "wind" in c.lower()][0]
    raw = df[col].values.astype(float)
    raw = raw / raw.max() if raw.max() > 0 else raw
    return raw * mw


def _load_load_profile(year: int, peak_kw: float = 4.0) -> np.ndarray:
    rng = np.random.default_rng(42)
    n = len(pd.read_parquet(DATAKIT / f"price_de_lu_{year}.parquet"))
    hourly = np.array([
        0.30, 0.25, 0.22, 0.20, 0.22, 0.28, 0.42, 0.60,
        0.65, 0.55, 0.48, 0.45, 0.50, 0.48, 0.45, 0.48,
        0.58, 0.72, 0.85, 0.90, 0.80, 0.65, 0.50, 0.38,
    ])
    pattern = np.tile(np.repeat(hourly, 4), -(-n // 96))[:n]
    noise = rng.normal(0, 0.04, n)
    return np.clip(pattern + noise, 0, None) * peak_kw


def _load_checkpoint() -> set:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return set(json.load(f))
    return set()


def _save_checkpoint(completed: set) -> None:
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(sorted(completed), f, indent=2)


def _scenario_id(project: str, params: dict) -> str:
    key = project + "|" + json.dumps(params, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _check_invariants(project: str, params: dict, result: dict) -> list:
    violations = []
    if project == "p02":
        turb = result.get("turb_mw", [])
        pump = result.get("pump_mw", [])
        if isinstance(turb, list) and isinstance(pump, list) and len(turb) and len(pump):
            t = np.array(turb); p_arr = np.array(pump)
            sim = int(np.sum((t > 0.01) & (p_arr > 0.01)))
            if sim > 0:
                violations.append(f"simultaneous pump+turb: {sim} steps")
    if project == "p01":
        cost = result.get("net_cost_eur", None)
        if cost is not None and cost < -15000:
            violations.append(f"suspicious net_cost {cost:.1f} EUR")
    return violations


# ============================================================
# P01: Residential PV+BESS
# ============================================================
def _p01_grid(smoke: bool) -> list:
    pv_kwp   = [4.0, 14.0] if smoke else [4.0, 6.0, 8.0, 10.0, 12.0, 14.0]
    bess_kwh = [0.0, 9.37] if smoke else [0.0, 5.0, 7.5, 9.37, 12.5, 15.0, 20.0]
    inv_kw   = [5.63]      if smoke else [3.68, 5.63, 8.0, 10.0]
    years    = [2025]      if smoke else [2024, 2025]
    return [{"pv_kwp": p, "bess_kwh": b, "inv_kw": i, "year": y}
            for p, b, i, y in itertools.product(pv_kwp, bess_kwh, inv_kw, years)]


def _p01_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "01-prosumer-pv-bess-mpc-rl" / "src"))
    from prosumer.config import RunConfig
    from prosumer.baselines.ladder import (
        b1_rule_based, b2_perfect_foresight, b3_rolling_mpc, make_forecasts,
    )
    from prosumer.market import settlement

    year     = params["year"]
    pv_kwp   = params["pv_kwp"]
    bess_kwh = params["bess_kwh"]
    inv_kw   = params["inv_kw"]

    price_raw = _load_price(year) / 1000.0
    price_import = (price_raw + 0.03 + 0.085 + 0.015 + 0.0205) * (1 + 0.19)
    price_export = np.full_like(price_import, 0.0786)
    pv   = _load_pv_profile(year, pv_kwp)
    load = _load_load_profile(year, peak_kw=3.5)
    n = min(len(price_import), len(pv), len(load))
    price_import = price_import[:n]; price_export = price_export[:n]
    pv = pv[:n]; load = load[:n]

    rc = RunConfig(dt=0.25, horizon_steps=96, terminal_value=False)
    if bess_kwh > 0.1:
        eff_bess = bess_kwh
        eff_soc_min  = max(bess_kwh * 0.1, 0.01)
        eff_soc_max  = bess_kwh * 0.9
        eff_soc_init = bess_kwh * 0.5
        eff_c_deg    = 0.03
    else:
        eff_bess     = 0.05
        eff_soc_min  = 0.005
        eff_soc_max  = 0.045
        eff_soc_init = 0.025
        eff_c_deg    = 999.0
    new_site = dataclasses.replace(
        rc.site,
        e_bess=eff_bess,
        soc_min=eff_soc_min,
        soc_max=eff_soc_max,
        soc_init=eff_soc_init,
        p_inv=inv_kw,
        c_deg=eff_c_deg,
    )
    rc = dataclasses.replace(rc, site=new_site)

    dt = rc.dt
    b1 = b1_rule_based(load, pv, dt, rc)
    b2 = b2_perfect_foresight(load, pv, price_import, price_export, dt, rc)
    load_fc, pv_fc = make_forecasts(load, pv, rc)
    # resolve_every=4 (hourly MPC update) ensures fast annual sweep
    b3 = b3_rolling_mpc(load, pv, price_import, price_export, load_fc, pv_fc, dt, rc, resolve_every=4)

    c1 = settlement.settle(b1, price_import, price_export, dt, rc.site)
    c2 = settlement.settle(b2, price_import, price_export, dt, rc.site) if b2 is not None else c1
    c3 = settlement.settle(b3, price_import, price_export, dt, rc.site)

    b1_cost = float(c1["net_cost"])
    b2_cost = float(c2["net_cost"])
    b3_cost = float(c3["net_cost"])
    net_cost = b3_cost

    tot_pv = float(np.sum(pv) * dt)
    exp_pv = float(np.sum(b3["p_exp"]) * dt)
    self_cons = float(max(0.0, 1.0 - exp_pv / max(tot_pv, 1e-6)))

    tot_load = float(np.sum(load) * dt)
    imp_grid = float(np.sum(b3["p_imp"]) * dt)
    grid_ind = float(max(0.0, 1.0 - imp_grid / max(tot_load, 1e-6)))

    peak_fi = float(np.max(b3["p_exp"]))
    curtail_kwh = float(np.sum(b3.get("curtail", np.zeros_like(pv))) * dt)
    gap = float((b1_cost - b3_cost) / (b1_cost - b2_cost)) if abs(b1_cost - b2_cost) > 1e-4 else 0.0

    return {
        "net_cost_eur": net_cost,
        "self_consumption_frac": self_cons,
        "grid_independence_frac": grid_ind,
        "peak_feedin_kw": peak_fi,
        "curtail_kwh": curtail_kwh,
        "b1_cost_eur": b1_cost,
        "b2_cost_eur": b2_cost,
        "b3_cost_eur": b3_cost,
        "gap_closure_frac": gap,
    }


# ============================================================
# P02: Pumped Storage Hydro
# ============================================================
def _p02_grid(smoke: bool) -> list:
    turb_mw   = [30.0]        if smoke else [20.0, 30.0, 40.0]
    e_max_mwh = [2400.0]      if smoke else [1200.0, 2400.0, 3600.0]
    wear      = [100]         if smoke else [0, 50, 100, 150, 200, 300]
    policies  = ["b1"]        if smoke else ["b1", "b2", "b3"]
    years     = [2025]        if smoke else [2024, 2025]
    return [{"turb_mw_unit": t, "e_max_mwh": e, "wear_eur": w, "policy": p, "year": y}
            for t, e, w, p, y in itertools.product(turb_mw, e_max_mwh, wear, policies, years)]


def _p02_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "02-pumped-storage-rl-multimarket" / "src"))
    from psw.config import PlantConfig
    from psw.baselines import (
        RunConfig, b1_price_threshold, b2_perfect, b3_rolling, make_price_forecast,
    )
    from psw.market import settle

    year  = params["year"]
    price = _load_price(year)
    e_max = params["e_max_mwh"]
    policy = params["policy"]

    plant = PlantConfig(
        n_units=2,
        p_turb_max_unit=params["turb_mw_unit"],
        p_turb_min_unit=params["turb_mw_unit"] * 0.25,
        p_pump_unit=params["turb_mw_unit"] * 0.9,
        e_max=e_max,
        e_min=e_max * 0.05,
        e_init=e_max * 0.5,
        mode_change_cost=float(params["wear_eur"]),
        e_reserve_low=e_max * 0.08,
        e_reserve_high=e_max * 0.95,
    )

    rc = dataclasses.replace(RunConfig(), plant=plant)

    # 14-day evaluation window (1,344 quarter-hours) of real German spot prices
    n_days = 14
    n_steps = n_days * 96
    price_14 = price[:n_steps]
    scale = 365.0 / n_days

    b1_res = b1_price_threshold(price_14, rc)
    b2_res = b2_perfect(price_14, rc)

    if policy == "b3":
        rng = np.random.default_rng(rc.seed)
        price_fc = make_price_forecast(price_14, rc, rng)
        b3_res = b3_rolling(price_14, price_fc, rc, resolve_every=4)
        active_res = b3_res
    elif policy == "b2":
        b3_res = None
        active_res = b2_res
    else:
        b3_res = None
        active_res = b1_res

    c_b1 = settle(b1_res, price_14, rc.dt, plant, rc.market)
    c_b2 = settle(b2_res, price_14, rc.dt, plant, rc.market)
    c_act = settle(active_res, price_14, rc.dt, plant, rc.market)
    c_b3 = settle(b3_res, price_14, rc.dt, plant, rc.market) if b3_res is not None else c_b2

    revenue   = float(c_act["net_revenue"] * scale)
    reversals = float(active_res.get("reversals_per_day", 0.0))
    wear_cost = float(c_act.get("wear_cost", 0.0) * scale)
    b1_rev = float(c_b1["net_revenue"] * scale)
    b2_rev = float(c_b2["net_revenue"] * scale)
    b3_rev = float(c_b3["net_revenue"] * scale)

    turb_arr  = active_res.get("turb_mw", [])
    pump_arr  = active_res.get("pump_mw", [])

    return {
        "revenue_eur": revenue,
        "reversals_per_day": reversals,
        "wear_cost_eur": wear_cost,
        "b1_revenue_eur": b1_rev,
        "b2_revenue_eur": b2_rev,
        "b3_revenue_eur": b3_rev,
        "turb_mw": list(turb_arr) if hasattr(turb_arr, "__iter__") and not isinstance(turb_arr, (int, float)) else [],
        "pump_mw": list(pump_arr) if hasattr(pump_arr, "__iter__") and not isinstance(pump_arr, (int, float)) else [],
    }


# ============================================================
# P03: Smart EV Charging
# ============================================================
def _p03_grid(smoke: bool) -> list:
    dim_limit  = [80.0]        if smoke else [40.0, 60.0, 80.0, 100.0, 140.0, 180.0, 220.0]
    sessions   = [48]          if smoke else [24, 36, 48, 64, 72]
    buffer_kwh = [0.0]         # Fixed to 0.0 as buffer storage is not modelled in evc site allocation
    years      = [2025]        if smoke else [2024, 2025]
    return [{"dim_limit_kw": d, "sessions_per_day": s, "buffer_kwh": b, "year": y}
            for d, s, b, y in itertools.product(dim_limit, sessions, buffer_kwh, years)]


def _p03_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "03-smart-ev-charging-14a" / "src"))
    from evc.sessions import generate as gen_sessions
    from evc.baselines import RunConfig, b0_uncontrolled, b3_price_greedy, cost as evc_cost

    year         = params["year"]
    dim_kw       = params["dim_limit_kw"]
    sess_per_day = params["sessions_per_day"]
    buffer_kwh   = params["buffer_kwh"]
    price        = _load_price(year) / 1000.0   # EUR/kWh
    n_days       = 14
    n_steps      = n_days * 96

    base_rc  = RunConfig(days=n_days)
    new_arch = dataclasses.replace(base_rc.archetype, arrivals_per_day=float(sess_per_day))
    new_site = dataclasses.replace(
        base_rc.site,
        buffer_kwh=buffer_kwh,
        buffer_kw=buffer_kwh / 2.0 if buffer_kwh > 0 else 0.0,
        n_connectors=max(int(sess_per_day * 1.2), 10),
    )
    rc = dataclasses.replace(base_rc, archetype=new_arch, site=new_site)

    price_14 = np.tile(price, -(-n_steps // len(price)))[:n_steps]
    hours_14 = np.tile(np.arange(96) // 4, n_days)[:n_steps].astype(float)
    dim_arr  = np.full(n_steps, dim_kw)

    sessions = gen_sessions(rc)
    r_b0 = b0_uncontrolled(sessions, n_steps, rc, price_14)
    r_b3 = b3_price_greedy(sessions, n_steps, rc, price_14, dim=dim_arr)

    c_b0_dict = evc_cost(r_b0, price_14, hours_14, rc)
    c_b3_dict = evc_cost(r_b3, price_14, hours_14, rc)
    cost_b0     = float(c_b0_dict.get("net_cost", c_b0_dict.get("energy_cost", 0.0)))
    cost_b3     = float(c_b3_dict.get("net_cost", c_b3_dict.get("energy_cost", 0.0)))
    missed_b3   = int(r_b3.get("missed_departures", 0))
    total_sess  = len(sessions)
    fulfil      = float(1.0 - missed_b3 / total_sess) if total_sess > 0 else 1.0
    curtail_kwh = float(max(0.0, r_b0.get("energy_kwh", 0.0) - r_b3.get("energy_kwh", 0.0)))
    peak_kw     = float(r_b3.get("peak_kw", 0.0))
    savings_pct = float((cost_b0 - cost_b3) / abs(cost_b0) * 100) if cost_b0 != 0 else 0.0

    return {
        "cost_b0_eur": cost_b0, "cost_b3_eur": cost_b3,
        "savings_pct": savings_pct, "departure_fulfillment_frac": fulfil,
        "missed_departures": missed_b3, "curtail_kwh": curtail_kwh,
        "peak_demand_kw": peak_kw,
    }


# ============================================================
# P04: Renewable Energy Community
# ============================================================
def _p04_grid(smoke: bool) -> list:
    c_net   = [0.02, 0.07]  if smoke else [0.000, 0.010, 0.020, 0.030, 0.050,
                                            0.070, 0.090, 0.100, 0.110, 0.120, 0.140]
    members = [10]          if smoke else [6, 10, 15, 20, 25, 30]
    pv_kwp  = [50.0]        if smoke else [30.0, 50.0, 65.0, 90.0, 120.0]
    years   = [2025]        if smoke else [2024, 2025]
    return [{"c_net": c, "n_members": m, "shared_pv_kwp": p, "year": y}
            for c, m, p, y in itertools.product(c_net, members, pv_kwp, years)]


def _p04_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "04-energy-sharing-rec" / "src"))
    from rec.community import (
        RunConfig, CommunityConfig, RegimeConfig, MemberConfig,
        pv_profile, member_profiles, allocate, settle,
    )
    from rec.game import (
        build_game, owen_allocation, core_excess, break_even_network_charge,
    )

    year   = params["year"]
    c_net  = params["c_net"]
    n_mem  = params["n_members"]
    pv_kwp = params["shared_pv_kwp"]
    n_days = 14

    rng = np.random.default_rng(42)
    demand_vals = rng.uniform(2500, 6000, n_mem)
    members = tuple(
        MemberConfig(
            name=f"m{i:02d}",
            annual_kwh=float(demand_vals[i]),
            pv_kwp=float(rng.choice([0.0, 0.0, 3.0, 5.0])),
        )
        for i in range(n_mem)
    )

    base_rc       = RunConfig(days=n_days)
    new_community = dataclasses.replace(base_rc.community, shared_pv_kwp=pv_kwp)
    new_regime    = dataclasses.replace(base_rc.regime, shared_network_charge=c_net)
    rc = dataclasses.replace(base_rc, community=new_community,
                             regime=new_regime, members=members)

    gen  = pv_profile(rc, kwp=pv_kwp)
    cons = member_profiles(rc)
    alloc = allocate(gen, cons, rc.mechanism, rc)
    result = settle(cons, alloc, gen, rc)

    community_bill     = float(result.get("community_bill", 0.0))
    community_baseline = float(result.get("community_baseline", 0.0))
    welfare_eur        = community_baseline - community_bill
    welfare_wk         = welfare_eur / (n_days / 7.0)
    consumer_saving    = float(np.mean(result.get("consumer_saving", [0.0])))
    owner_gain         = float(result.get("owner_gain", 0.0))
    self_cons          = float(result.get("self_consumption", 0.0)) * 100
    shared_kwh         = float(result.get("shared_kwh", 0.0))
    breakeven          = float(break_even_network_charge(rc))

    # Cooperative game evaluation: Owen core stability & blocking coalitions
    game = build_game(rc, cons, gen)
    x_opt = result.get("member_payoff", np.zeros(n_mem))
    if n_mem <= 12:
        from rec.game import all_coalition_values
        v_all = all_coalition_values(game)
        c_stab = core_excess(game, x_opt, values=v_all)
    else:
        c_stab = core_excess(game, x_opt, values=None, n_sample=500)

    blocking_coalitions = int(c_stab.get("blocking_coalitions", 0))
    in_core = int(c_stab.get("in_core", True))

    return {
        "welfare_eur_per_week": welfare_wk,
        "consumer_saving_eur": consumer_saving,
        "owner_gain_eur": owner_gain,
        "self_consumption_pct": self_cons,
        "shared_kwh_per_day": shared_kwh / n_days,
        "breakeven_tariff_eur_kwh": breakeven,
        "blocking_coalitions": blocking_coalitions,
        "in_core": in_core,
    }


# ============================================================
# P05: Utility Hybrid Wind+PV+BESS
# ============================================================
def _p05_grid(smoke: bool) -> list:
    poc_mw   = [40.0]       if smoke else [25.0, 30.0, 40.0, 50.0, 60.0]
    pv_mw    = [30.0]       if smoke else [20.0, 30.0, 40.0, 50.0, 60.0]
    bess_mwh = [20.0]       if smoke else [0.0, 10.0, 20.0, 30.0, 40.0]
    years    = [2025]       if smoke else [2024, 2025]
    return [{"poc_mw": p, "pv_mw": pv, "bess_mwh": b, "year": y}
            for p, pv, b, y in itertools.product(poc_mw, pv_mw, bess_mwh, years)]


def _p05_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "05-utility-hybrid-plant-dispatch" / "src"))
    from hybrid.baselines import (
        RunConfig as RC5, b0_no_battery, b1_curtailment_avoidance, b2_perfect,
        b3_rolling, make_forecasts, effective_price, settle,
    )

    year     = params["year"]
    poc_mw   = params["poc_mw"]
    pv_mw    = params["pv_mw"]
    bess_mwh = params["bess_mwh"]
    wind_mw  = 40.0

    price = _load_price(year)
    wind  = _load_wind_profile(year, wind_mw)
    pv    = _load_pv_profile(year, pv_mw)
    n_days = 14
    n_steps = n_days * 96
    scale = 365.0 / n_days

    price = price[:n_steps]; wind = wind[:n_steps]; pv = pv[:n_steps]

    base_rc = RC5(dt=0.25, horizon_steps=48)
    new_plant = dataclasses.replace(
        base_rc.plant,
        wind_mw=wind_mw,
        pv_mw=pv_mw,
        bess_mwh=bess_mwh,
        bess_mw=bess_mwh / 2.0 if bess_mwh > 0 else 0.0,
        conn_mw=poc_mw,
    )
    rc = dataclasses.replace(base_rc, plant=new_plant)

    premium_rate = 73.5
    eff = effective_price(price, rc.market, premium_rate)
    rng = np.random.default_rng(rc.seed)
    wind_fc, pv_fc, price_fc = make_forecasts(wind, pv, eff, rc, rng)

    r_b0 = b0_no_battery(wind, pv, rc)
    r_b1 = b1_curtailment_avoidance(wind, pv, rc)
    # resolve_every=4 provides rapid parameter sweeps
    r_b3 = b3_rolling(wind, pv, eff, wind_fc, pv_fc, price_fc, rc, resolve_every=4)

    c_b0 = settle(r_b0, price, rc.dt, rc.plant, rc.market, premium_rate=premium_rate)
    c_b1 = settle(r_b1, price, rc.dt, rc.plant, rc.market, premium_rate=premium_rate)
    c_b3 = settle(r_b3, price, rc.dt, rc.plant, rc.market, premium_rate=premium_rate)

    revenue   = float(c_b3["net_revenue"] * scale)
    curtail   = float(c_b3["forced_curtail_mwh"] * scale)
    neg_avoid = float(c_b3.get("neg_price_avoided_eur", 0.0) * scale)
    overplant = (wind_mw + pv_mw) / poc_mw
    b1_rev = float(c_b1["net_revenue"] * scale)
    b3_rev = revenue
    lift_pct = float((b3_rev - b1_rev) / abs(b1_rev) * 100) if b1_rev != 0 else 0.0

    return {
        "revenue_eur": revenue,
        "curtail_mwh": curtail,
        "neg_price_avoided_eur": neg_avoid,
        "overplanting_factor": overplant,
        "b1_revenue_eur": b1_rev,
        "b3_revenue_eur": b3_rev,
        "revenue_lift_pct": lift_pct,
    }


# ============================================================
# P06: Probabilistic Forecast-to-Bid
# ============================================================
def _p06_grid(smoke: bool) -> list:
    tau        = [0.42]       if smoke else [0.15, 0.25, 0.35, 0.42, 0.50, 0.60, 0.70, 0.85]
    sigma_base = [0.05]       if smoke else [0.02, 0.04, 0.059, 0.075, 0.10]
    rebap_asym = [42.0]       if smoke else [15.0, 30.0, 42.0, 60.0, 80.0]
    years      = [2025]       if smoke else [2024, 2025]
    return [{"tau": t, "sigma_base": s, "rebap_asymmetry": r, "year": y}
            for t, s, r, y in itertools.product(tau, sigma_base, rebap_asym, years)]


def _p06_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "06-probabilistic-forecast-to-bid" / "src"))
    from f2b.policies import RunConfig, b0_day_ahead_only, b3_stochastic_mpc, settle
    from f2b.forecast import build_store, ForecastConfig

    year       = params["year"]
    tau        = params["tau"]
    sigma_base = params["sigma_base"]
    rebap_asym = params["rebap_asymmetry"]

    price_da = _load_price(year)
    n = len(price_da)

    new_forecast = dataclasses.replace(ForecastConfig(), sigma_base=sigma_base)
    rc = dataclasses.replace(RunConfig(), forecast=new_forecast)

    truth = (_load_wind_profile(year, 200.0) + _load_pv_profile(year, 100.0))[:n]
    capacity = 300.0
    rng_fc = np.random.default_rng(42)
    store = build_store(truth=truth, cfg=rc.forecast, dt=rc.dt,
                        max_lead=48, rng=rng_fc, capacity=capacity)

    rng = np.random.default_rng(42)
    rebap = rng.normal(rebap_asym * 0.3, rebap_asym, n)
    rebap_short = float(np.percentile(rebap, 60))
    rebap_long  = float(np.percentile(rebap, 40))
    price_id    = price_da + rng.normal(0, 5, n)

    pos_b0, trades_b0 = b0_day_ahead_only(store, n, rc)
    r_b0 = settle(pos_b0, trades_b0, truth, price_da, rebap, price_id, rc)

    pos_b3, trades_b3 = b3_stochastic_mpc(
        store, n, rc,
        rebap_mean_short=rebap_short,
        rebap_mean_long=rebap_long,
        deadband_k=tau,
    )
    r_b3 = settle(pos_b3, trades_b3, truth, price_da, rebap, price_id, rc)

    capture_b0 = float(r_b0.get("capture_eur_mwh", 0.0))
    capture_b3 = float(r_b3.get("capture_eur_mwh", 0.0))
    net_gain   = float(r_b3.get("net_gain_eur", 0.0))
    imb_b0     = max(float(r_b0.get("imbalance_exposure_eur", 1.0)), 1.0)
    imb_b3     = float(r_b3.get("imbalance_exposure_eur", 1.0))
    risk_red   = float(max(0.0, 1.0 - imb_b3 / imb_b0)) * 100

    return {
        "capture_price_b0_eur_mwh": capture_b0, "capture_price_b3_eur_mwh": capture_b3,
        "net_gain_30d_eur": net_gain, "imbalance_risk_reduction_pct": risk_red,
    }


# ============================================================
# Worker
# ============================================================
PROJECT_RUNNERS = {
    "p01": _p01_run, "p02": _p02_run, "p03": _p03_run,
    "p04": _p04_run, "p05": _p05_run, "p06": _p06_run,
}


def _run_scenario(task: tuple) -> dict:
    project, params, sid = task
    t0 = time.perf_counter()
    try:
        result = PROJECT_RUNNERS[project](params)
        elapsed = time.perf_counter() - t0
        violations = _check_invariants(project, params, result)
        row = {"project": project, "sid": sid, "elapsed_s": round(elapsed, 3),
               "status": "ok", "violation": "|".join(violations), **params}
        row.update({f"out_{k}": v for k, v in result.items()
                    if not isinstance(v, list)})
        return row
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        return {"project": project, "sid": sid, "elapsed_s": round(elapsed, 3),
                "status": "error", "violation": "", "error": f"{exc}\n{tb}", **params}


# ============================================================
# Batch engine
# ============================================================
def build_tasks(smoke: bool) -> list:
    grids = {
        "p01": _p01_grid(smoke), "p02": _p02_grid(smoke), "p03": _p03_grid(smoke),
        "p04": _p04_grid(smoke), "p05": _p05_grid(smoke), "p06": _p06_grid(smoke),
    }
    tasks = []
    for proj, grid in grids.items():
        for params in grid:
            sid = _scenario_id(proj, params)
            tasks.append((proj, params, sid))
    return tasks


def run_batch(smoke: bool, n_workers: int) -> None:
    tasks     = build_tasks(smoke)
    completed = _load_checkpoint()
    pending       = [t for t in tasks if t[2] not in completed]
    total         = len(tasks)
    done_already  = total - len(pending)

    log.info("Total scenarios: %d  |  Already done: %d  |  Pending: %d",
             total, done_already, len(pending))

    rows_by_proj = {p: [] for p in ["p01","p02","p03","p04","p05","p06"]}
    for proj in rows_by_proj:
        pq = RESULTS / f"{proj}_scenarios.parquet"
        if pq.exists():
            rows_by_proj[proj].extend(pd.read_parquet(pq).to_dict("records"))

    if not pending:
        log.info("All scenarios already completed. Rebuilding outputs.")
    else:
        violations_all = []
        n_done = done_already
        save_every = 20

        with concurrent.futures.ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_run_scenario, t): t for t in pending}
            for fut in concurrent.futures.as_completed(futures):
                task = futures[fut]
                proj = task[0]; sid = task[2]
                try:
                    row = fut.result(timeout=600)
                except Exception as exc:
                    log.error("Future exception %s %s: %s", proj, sid, exc)
                    row = {"project": proj, "sid": sid, "status": "error",
                           "error": str(exc), **task[1]}

                rows_by_proj[proj].append(row)
                n_done += 1

                if row.get("status") == "ok":
                    completed.add(sid)
                else:
                    log.error("FAILED %s %s: %.150s", proj, sid, row.get("error", ""))

                if row.get("violation"):
                    violations_all.append({"sid": sid, "project": proj,
                                           "violation": row["violation"], "params": task[1]})
                    log.warning("INVARIANT VIOLATION %s %s: %s", proj, sid, row["violation"])

                if row.get("status") == "ok":
                    log.info("[%d/%d] OK  %-4s  %.2fs  %s",
                             n_done, total, proj, row.get("elapsed_s", 0),
                             json.dumps(task[1]))

                if n_done % save_every == 0 or n_done == total:
                    _save_checkpoint(completed)
                    for p, rows in rows_by_proj.items():
                        if rows:
                            pd.DataFrame(rows).to_parquet(
                                RESULTS / f"{p}_scenarios.parquet", index=False)

        _save_checkpoint(completed)
        for p, rows in rows_by_proj.items():
            if rows:
                pd.DataFrame(rows).to_parquet(RESULTS / f"{p}_scenarios.parquet", index=False)

        with open(AUDIT_FILE, "w") as f:
            json.dump({"total_scenarios": total, "completed": len(completed),
                       "violations": len(violations_all),
                       "violation_details": violations_all}, f, indent=2)

    _build_lookup_cubes()
    log.info("Done. Results: %s", RESULTS)


def _build_lookup_cubes() -> None:
    log.info("Building lookup_cubes.json ...")
    cubes: dict = {}

    key_cols = {
        "p01": ["pv_kwp", "bess_kwh", "inv_kw", "year"],
        "p02": ["turb_mw_unit", "e_max_mwh", "wear_eur", "policy", "year"],
        "p03": ["dim_limit_kw", "sessions_per_day", "buffer_kwh", "year"],
        "p04": ["c_net", "n_members", "shared_pv_kwp", "year"],
        "p05": ["poc_mw", "pv_mw", "bess_mwh", "year"],
        "p06": ["tau", "sigma_base", "rebap_asymmetry", "year"],
    }
    metric_cols = {
        "p01": ["out_net_cost_eur", "out_self_consumption_frac", "out_grid_independence_frac",
                "out_b1_cost_eur", "out_b3_cost_eur", "out_gap_closure_frac"],
        "p02": ["out_revenue_eur", "out_reversals_per_day", "out_wear_cost_eur",
                "out_b1_revenue_eur", "out_b3_revenue_eur"],
        "p03": ["out_cost_b0_eur", "out_cost_b3_eur", "out_savings_pct",
                "out_departure_fulfillment_frac", "out_curtail_kwh"],
        "p04": ["out_welfare_eur_per_week", "out_consumer_saving_eur",
                "out_owner_gain_eur", "out_self_consumption_pct", "out_breakeven_tariff_eur_kwh"],
        "p05": ["out_revenue_eur", "out_curtail_mwh", "out_overplanting_factor",
                "out_b1_revenue_eur", "out_b3_revenue_eur", "out_revenue_lift_pct"],
        "p06": ["out_capture_price_b3_eur_mwh", "out_net_gain_30d_eur",
                "out_imbalance_risk_reduction_pct"],
    }

    for proj in ["p01","p02","p03","p04","p05","p06"]:
        pq = RESULTS / f"{proj}_scenarios.parquet"
        if not pq.exists():
            cubes[proj] = {}; continue
        df = pd.read_parquet(pq)
        df = df[df["status"] == "ok"]
        keys = key_cols[proj]
        mets = [m for m in metric_cols[proj] if m in df.columns]
        if not mets:
            cubes[proj] = {}; continue
        proj_cube = {
            "keys": keys,
            "metrics": [m.replace("out_", "") for m in mets],
            "data": {},
        }
        for _, row in df.iterrows():
            k = "|".join(str(row.get(c, "")) for c in keys)
            proj_cube["data"][k] = [
                round(float(row.get(m, 0.0)), 4) if pd.notna(row.get(m)) else None
                for m in mets
            ]
        cubes[proj] = proj_cube
        log.info("Cube %s: %d rows", proj, len(proj_cube["data"]))

    with open(RESULTS / "lookup_cubes.json", "w") as f:
        json.dump(cubes, f, separators=(",", ":"))
    sz = (RESULTS / "lookup_cubes.json").stat().st_size
    log.info("lookup_cubes.json written (%d bytes)", sz)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--smoke-test", action="store_true")
    grp.add_argument("--full", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    args = ap.parse_args()

    log.info("=" * 72)
    log.info("Portfolio Overnight Simulation Runner")
    log.info("Mode: %s  |  Workers: %d", "SMOKE" if args.smoke_test else "FULL", args.workers)
    log.info("Results dir: %s", RESULTS)
    log.info("=" * 72)

    run_batch(smoke=args.smoke_test, n_workers=args.workers)

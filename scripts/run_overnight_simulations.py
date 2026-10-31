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


def _get_git_commit() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"



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
    bess_kwh = [0.0, 9.37] if smoke else [0.0, 2.0, 3.5, 5.0, 7.5, 9.37, 12.5, 15.0, 20.0]
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
        "eval_days": n // 96,
        "annualised": False,
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
# ============================================================
def _p02_grid(smoke: bool) -> list:
    poc_mw   = [40.0]       if smoke else [25.0, 30.0, 40.0, 50.0, 60.0]
    pv_mw    = [30.0]       if smoke else [20.0, 30.0, 40.0, 50.0, 60.0]
    bess_mwh = [20.0]       if smoke else [0.0, 10.0, 20.0, 30.0, 40.0]
    years    = [2025]       if smoke else [2024, 2025]
    return [{"poc_mw": p, "pv_mw": pv, "bess_mwh": b, "year": y}
            for p, pv, b, y in itertools.product(poc_mw, pv_mw, bess_mwh, years)]


def _p02_run(params: dict) -> dict:
    sys.path.insert(0, str(ROOT / "projects" / "02-utility-hybrid-plant-dispatch" / "src"))
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
    neg_avoid = float(np.sum(r_b3["chosen_curtail"] * np.maximum(-price, 0.0)) * rc.dt * scale)
    overplant = (wind_mw + pv_mw) / poc_mw
    b1_rev = float(c_b1["net_revenue"] * scale)
    b3_rev = revenue
    lift_pct = float((b3_rev - b1_rev) / abs(b1_rev) * 100) if b1_rev != 0 else 0.0

    return {
        "eval_days": n_days,
        "annualised": True,
        "revenue_eur_per_year_extrapolated": revenue,
        "curtail_mwh_per_year_extrapolated": curtail,
        "neg_price_avoided_eur_per_year_extrapolated": neg_avoid,
        "overplanting_factor": overplant,
        "b1_revenue_eur_per_year_extrapolated": b1_rev,
        "b3_revenue_eur_per_year_extrapolated": b3_rev,
        "revenue_lift_pct": lift_pct,
    }


# ============================================================
# Worker
# ============================================================
PROJECT_RUNNERS = {
    "p01": _p01_run, "p02": _p02_run,
}


def _run_scenario(task: tuple) -> dict:
    project, params, sid = task
    t0 = time.perf_counter()
    try:
        result = PROJECT_RUNNERS[project](params)
        elapsed = time.perf_counter() - t0
        violations = _check_invariants(project, params, result)
        row = {"project": project, "sid": sid, "elapsed_s": round(elapsed, 3),
               "status": "ok", "violation": "|".join(violations),
               "seed": int(params.get("seed", 42)),
               "git_commit": _get_git_commit(),
               "eval_days": result.get("eval_days", 366 if params.get("year") == 2024 else 365),
               "annualised": result.get("annualised", False),
               **params}
        row.update({f"out_{k}": v for k, v in result.items()
                    if not isinstance(v, list)})
        return row
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        return {"project": project, "sid": sid, "elapsed_s": round(elapsed, 3),
                "status": "error", "violation": "", "error": f"{exc}\n{tb}",
                "seed": int(params.get("seed", 42)),
                "git_commit": _get_git_commit(),
                "eval_days": 366 if params.get("year") == 2024 else 365,
                "annualised": False,
                **params}


KEY_COLS = {
    "p01": ["pv_kwp", "bess_kwh", "inv_kw", "year"],
    "p02": ["poc_mw", "pv_mw", "bess_mwh", "year"],
}
METRIC_COLS = {
    "p01": ["out_net_cost_eur", "out_self_consumption_frac", "out_grid_independence_frac",
            "out_b1_cost_eur", "out_b3_cost_eur", "out_gap_closure_frac"],
    "p02": ["out_revenue_eur_per_year_extrapolated", "out_curtail_mwh_per_year_extrapolated", "out_overplanting_factor",
            "out_b1_revenue_eur_per_year_extrapolated", "out_b3_revenue_eur_per_year_extrapolated", "out_revenue_lift_pct",
            "out_neg_price_avoided_eur_per_year_extrapolated"],
}


# ============================================================
# Batch engine
# ============================================================
def build_tasks(smoke: bool, project_filter: str | None = None) -> list:
    grids = {
        "p01": _p01_grid(smoke), "p02": _p02_grid(smoke),
    }
    if project_filter:
        grids = {project_filter: grids[project_filter]}
    tasks = []
    for proj, grid in grids.items():
        for params in grid:
            sid = _scenario_id(proj, params)
            tasks.append((proj, params, sid))
    return tasks


def run_batch(smoke: bool, n_workers: int, project_filter: str | None = None,
              reset_p02: bool = False) -> None:
    tasks     = build_tasks(smoke, project_filter=project_filter)
    completed = _load_checkpoint()
    commit    = _get_git_commit()

    rows_by_proj = {p: [] for p in PROJECT_RUNNERS}
    for proj in rows_by_proj:
        pq = RESULTS / f"{proj}_scenarios.parquet"
        if pq.exists():
            df = pd.read_parquet(pq)
            if "seed" not in df.columns:
                df["seed"] = 42
            if "git_commit" not in df.columns:
                df["git_commit"] = commit
            rows_by_proj[proj].extend(df.to_dict("records"))

    if reset_p02 or project_filter == "p02":
        log.info("Resetting P02 scenario cache and completed IDs for re-run.")
        p02_sids = {_scenario_id("p02", p) for p in _p02_grid(smoke)}
        completed.difference_update(p02_sids)
        rows_by_proj["p02"] = []

    pending       = [t for t in tasks if t[2] not in completed]
    total         = len(tasks)
    done_already  = total - len(pending)

    log.info("Total scenarios: %d  |  Already done: %d  |  Pending: %d",
             total, done_already, len(pending))

    if not pending:
        log.info("All scenarios already completed. Rebuilding outputs.")
        for p, rows in rows_by_proj.items():
            if rows:
                pd.DataFrame(rows).to_parquet(RESULTS / f"{p}_scenarios.parquet", index=False)
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

    # Validation check across the projects in the grid: fail loudly if any metric column is constant 0.0
    for p in PROJECT_RUNNERS:
        rows = rows_by_proj.get(p, [])
        if rows:
            df_p = pd.DataFrame(rows)
            for m in METRIC_COLS.get(p, []):
                if m in df_p.columns:
                    series = df_p[m].dropna()
                    if len(series) > 0 and (series == 0.0).all():
                        raise AssertionError(
                            f"FATAL: Metric column '{m}' for project '{p}' is identically 0.0 across all {len(series)} scenarios! "
                            "Simulation run failed non-zero validation check."
                        )

    _build_lookup_cubes()
    log.info("Done. Results: %s", RESULTS)


def _build_lookup_cubes() -> None:
    log.info("Building lookup_cubes.json ...")
    cubes: dict = {}
    key_cols = KEY_COLS
    metric_cols = METRIC_COLS

    for proj in PROJECT_RUNNERS:
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
    ap.add_argument("--project", type=str, choices=list(PROJECT_RUNNERS), default=None,
                    help="Run scenarios only for a specific project")
    ap.add_argument("--reset-p02", action="store_true", help="Force re-run of P02 scenarios")
    args = ap.parse_args()

    log.info("=" * 72)
    log.info("Portfolio Overnight Simulation Runner")
    log.info("Mode: %s  |  Workers: %d", "SMOKE" if args.smoke_test else "FULL", args.workers)
    if args.project:
        log.info("Project filter: %s", args.project)
    log.info("Results dir: %s", RESULTS)
    log.info("=" * 72)

    run_batch(smoke=args.smoke_test, n_workers=args.workers,
              project_filter=args.project,
              reset_p02=args.reset_p02)

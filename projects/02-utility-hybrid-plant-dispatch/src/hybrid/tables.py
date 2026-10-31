"""Tidy result tables, as the commands write them with --out: one row per scenario and rung.

A row carries what its numbers depend on: every settlement term, the window, the premium it
was settled on and the settings it ran with. A figure quoted from a row therefore needs no
other file to be read or re-run.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import RunConfig

B3 = "B3 rolling MPC"

# The settings a row records, by the config class that holds them. A test keeps these lists
# and INERT_SETTINGS together equal to the classes' fields, so a new setting needs a decision.
PLANT_SETTINGS = ("wind_mw", "pv_mw", "bess_mw", "bess_mwh", "conn_mw", "soc_min_frac",
                  "soc_max_frac", "soc_init_frac", "eta_c", "eta_d", "c_deg_eur_mwh",
                  "grid_charging_allowed")
MARKET_SETTINGS = ("eeg_enabled", "anzulegender_wert_wind", "anzulegender_wert_pv",
                   "direct_marketing_fee", "negative_price_rule")
# B3's settings are recorded only where B3 ran; they change nothing for B0 to B2.
B3_SETTINGS = ("horizon_steps", "terminal_value", "terminal_price", "forecast_sigma",
               "forecast_rho", "forecast_price_scale", "seed")
# Declared, but no command reads them, so a row that listed them would suggest they were
# modelled: the ramp limit; the aFRR fields, as aFRR is not built yet; the redispatch fields,
# as no command applies an order; and the reBAP fields, which settle nothing because no rung
# commits a schedule (`baselines.run_ladder`).
INERT_SETTINGS = ("ramp_mw_per_step", "afrr_enabled", "afrr_capacity_eur_mw_h",
                  "afrr_block_steps", "redispatch_prob_per_step", "redispatch_depth",
                  "imbalance_enabled", "rebap_sigma")


def window_columns(inputs: str, year: int | None, steps: int, dt: float,
                   index: pd.DatetimeIndex | None = None) -> dict:
    """Columns that place a result in time.

    inputs: where the series come from, e.g. "Energy-Charts".
    year: the calendar year of the window, or None for series without dates.
    steps: the window's length in steps. dt: step length in hours, e.g. 0.25.
    index: the window's tz-aware timestamps, or None for series without dates.
    Returns {"inputs", "year", the window's start and end (the first instant after it) in UTC
    and in Berlin time as ISO 8601, "days" of 24 h, "steps", "dt_h"}.
    """
    cols = {"inputs": inputs, "year": year}
    start = end = None
    if index is not None:
        start, end = index[0], index[-1] + pd.Timedelta(hours=dt)
    for zone, tz in (("utc", "UTC"), ("berlin", "Europe/Berlin")):
        for name, stamp in (("start", start), ("end", end)):
            cols[f"window_{name}_{zone}"] = (None if stamp is None
                                             else stamp.tz_convert(tz).isoformat())
    cols.update({"days": steps * dt / 24, "steps": steps, "dt_h": dt})
    return cols


def settings(run: RunConfig, with_b3: bool) -> dict:
    """The settings a result ran with, as columns: plant_*, market_*, and B3's where B3 ran."""
    cols = {f"plant_{name}": getattr(run.plant, name) for name in PLANT_SETTINGS}
    cols.update({f"market_{name}": getattr(run.market, name) for name in MARKET_SETTINGS})
    if with_b3:
        cols.update({name: getattr(run, name) for name in B3_SETTINGS})
    return cols


def rung_rows(res: dict, run: RunConfig, window: dict, premium: dict,
              **scenario) -> list[dict]:
    """One row per rung of a `baselines.run_ladder` result.

    res: run_ladder's output, {rung name: dispatch arrays with "cost" and "violations"}.
    run: the settings it ran with. window: `window_columns(...)` of its window.
    premium: columns on the premium it was settled on, e.g. {"premium_period": "year", ...}.
    scenario: columns that name the scenario, e.g. conn_mw=20.0, or hold its results across
        rungs, e.g. battery_value_eur=21_841.0; they repeat on every rung's row.
    Returns one dict per rung, in the ladder's order: the scenario columns, "rung", every
    settlement term of `market.settle` (EUR terms end in _eur), the number of constraint
    violations, the energy stored after the last step (MWh), B3's mean and maximum solve time
    where B3 ran (on its own row; empty on the others), the premium, the window and the
    settings.
    """
    with_b3 = B3 in res
    rows = []
    for rung, r in res.items():
        row = {**scenario, "rung": rung}
        row.update({k if k.endswith("_mwh") else f"{k}_eur": v for k, v in r["cost"].items()})
        row["violations"] = int(sum(r["violations"].values()))
        row["final_soc_mwh"] = float(r["soc"][-1])
        if with_b3:
            times = r.get("solve_time_s") if rung == B3 else None
            row["b3_solve_time_mean_s"] = None if times is None else float(np.mean(times))
            row["b3_solve_time_max_s"] = None if times is None else float(np.max(times))
        rows.append({**row, **premium, **window, **settings(run, with_b3)})
    return rows


def write_csv(rows: list[dict], path: str | Path) -> Path:
    """Write tidy rows to a CSV at `path`, creating its folder. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # one line ending on every system, so that a rerun elsewhere diffs only where numbers moved
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")
    return path

"""Tidy result tables, as the commands write them with --out: one row per scenario and rung.

A row carries what its numbers depend on: every settlement term, the window, the premium it
was settled on and the settings it ran with. A figure quoted from a row therefore needs no
other file to be read or re-run. `merge_csv` adds rows to a table that several processes
share, as the report's run metadata is.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .baselines import ladder_tolerance, rung_plant
from .config import PlantConfig, RunConfig
from .reserve import CapacityPrices

B2, B3 = "B2 perfect foresight", "B3 rolling MPC"

# The settings a row records, by the config class that holds them. A test keeps these lists
# and INERT_SETTINGS together equal to the classes' fields, so a new setting needs a decision.
PLANT_SETTINGS = ("wind_mw", "pv_mw", "bess_mw", "bess_mwh", "conn_mw", "soc_min_frac",
                  "soc_max_frac", "soc_init_frac", "eta_c", "eta_d", "c_deg_eur_mwh",
                  "grid_charging_allowed")
MARKET_SETTINGS = ("eeg_enabled", "anzulegender_wert_wind", "anzulegender_wert_pv",
                   "direct_marketing_fee", "negative_price_rule")
# The aFRR settings are recorded only where aFRR is enabled, as market_<name>, like B3's
# below: with it off they change no result, and the rows of every run without aFRR keep the
# columns they had.
AFRR_SETTINGS = ("afrr_enabled", "afrr_price_basis", "afrr_energy_hours_up",
                 "afrr_energy_hours_down", "afrr_floor_hours", "afrr_shortfall_penalty_eur_mwh")
# B3's settings are recorded only where B3 ran, as b3_<name>: they change nothing for the
# other rungs, whose rows in that run carry them as well.
B3_SETTINGS = ("horizon_steps", "terminal_value", "terminal_price", "forecast_sigma",
               "forecast_rho", "forecast_price_scale", "seed")
# B3's aFRR settings, recorded only where B3 ran with aFRR enabled, as b3_<name>.
B3_AFRR_SETTINGS = ("afrr_forecast_margin_sigmas",)
# Declared, but no command reads them, so a row that listed them would suggest they were
# modelled: the ramp limit; the redispatch fields, as no command applies an order; and the
# reBAP fields, which settle nothing because no rung commits a schedule
# (`baselines.run_ladder`).
INERT_SETTINGS = ("ramp_mw_per_step", "redispatch_prob_per_step", "redispatch_depth",
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


def settings(run: RunConfig, with_b3: bool, plant: PlantConfig | None = None) -> dict:
    """The settings a result ran with, as columns: plant_*, market_* (the aFRR ones only
    where run.market.afrr_enabled is True), and b3_* where B3 ran (its aFRR one only where
    aFRR is enabled as well).

    run: the run's settings. with_b3: whether B3 ran in it.
    plant: the plant the row's rung ran on (`baselines.rung_plant`), run.plant by default.
    """
    plant = run.plant if plant is None else plant
    cols = {f"plant_{name}": getattr(plant, name) for name in PLANT_SETTINGS}
    # the start soc_init_frac stands for, in MWh: None starts the battery at its minimum
    cols["plant_soc_init_mwh"] = plant.soc_init
    cols.update({f"market_{name}": getattr(run.market, name) for name in MARKET_SETTINGS})
    if run.market.afrr_enabled:
        cols.update({f"market_{name}": getattr(run.market, name) for name in AFRR_SETTINGS})
    if with_b3:
        cols.update({f"b3_{name}": getattr(run, name) for name in B3_SETTINGS})
    if with_b3 and run.market.afrr_enabled:
        cols.update({f"b3_{name}": getattr(run, name) for name in B3_AFRR_SETTINGS})
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
    violations, the ladder's tolerance (`baselines.ladder_tolerance`), the energy stored after
    the last step (MWh); where B3 ran, on B3's row and empty on the others, its mean and
    maximum solve time and the values it ran on where its settings leave them to the run: the
    price error's scale and the mean terminal price of its plans; where aFRR ran
    (run.market.afrr_enabled), what each rung reserved (`_afrr_columns`); then the premium,
    the window and the settings, with the plant the rung ran on: B0 and B2 no battery ran
    without the battery.
    """
    with_b3 = B3 in res
    b2 = res["B2 perfect foresight"]["cost"]
    # the bound run_ladder held every rung to, on B2's revenue before imbalance: rungs closer
    # than this differ by the solver's rounding, and B3 may sit above B2 by as much
    tolerance = ladder_tolerance(b2["net_revenue"] + b2["imbalance_cost"])
    rows = []
    for rung, r in res.items():
        row = {**scenario, "rung": rung}
        row.update({k if k.endswith("_mwh") else f"{k}_eur": v for k, v in r["cost"].items()})
        row["violations"] = int(sum(r["violations"].values()))
        row["ladder_tolerance_eur"] = tolerance
        row["final_soc_mwh"] = float(r["soc"][-1])
        if with_b3:
            row.update(_b3_columns(r if rung == B3 else {}))
        if run.market.afrr_enabled:
            row.update(_afrr_columns(rung, r, run.dt, with_b3))
        rows.append({**row, **premium, **window,
                     **settings(run, with_b3, rung_plant(rung, run.plant))})
    return rows


def _b3_columns(r: dict) -> dict:
    """B3's measured values as columns, from its dispatch `r`: the mean and maximum solve time
    (s), the scale its price error was sized on and its plans' mean terminal price (EUR/MWh).
    Each is None where `r` lacks it, as on the other rungs' rows (r = {})."""
    times = r.get("solve_time_s")
    prices = r.get("terminal_price_eur_mwh")
    return {"b3_solve_time_mean_s": None if times is None else float(np.mean(times)),
            "b3_solve_time_max_s": None if times is None else float(np.max(times)),
            "b3_price_error_scale_eur_mwh": r.get("price_error_scale_eur_mwh"),
            "b3_terminal_price_mean_eur_mwh": None if prices is None else float(np.mean(prices))}


def _afrr_columns(rung: str, r: dict, dt: float, with_b3: bool) -> dict:
    """What one rung reserved in a run with aFRR, as columns, from its dispatch `r`.

    Every rung: per direction the MW it held, as the mean over the window's steps, and the
    MW-hours it held; B0, B1 and B2 no battery hold none, so 0. On B2's row, and empty on the
    others, its rounding bound (EUR, `reserve.rounding_bound`). Where B3 ran, on B3's row and
    empty on the others, its bids and its gates (`_b3_afrr_columns`).
    rung: the rung's name. dt: step length in hours. with_b3: whether B3 ran.
    """
    zeros = np.zeros(len(r["export"]))
    cols = {}
    for name in ("up", "down"):
        held = np.asarray(r.get(f"afrr_{name}", zeros), dtype=float)
        cols[f"afrr_{name}_mean_mw"] = float(np.mean(held))
        cols[f"afrr_{name}_mw_h"] = float(np.sum(held) * dt)
    cols["afrr_rounding_bound_eur"] = r["afrr_rounding_bound_eur"] if rung == B2 else None
    if with_b3:
        cols.update(_b3_afrr_columns(r if rung == B3 else {}, dt))
    return cols


def _b3_afrr_columns(r: dict, dt: float) -> dict:
    """B3's aFRR bids and gates as columns, from its dispatch `r`, or all empty where r = {}:
    the MW-hours it bid per direction before each award; its gates (afrr_committed_days), one
    per day after the window's first, each counted whether its plan was solved and whether it
    bid any MW; of them those not solved, which bid nothing; the re-plans that held reserve
    and were not solved; and the mean and maximum seconds of a gate's solve, empty on a
    window with no gate. dt: step length in hours."""
    if not r:
        return dict.fromkeys(("afrr_bid_up_mw_h", "afrr_bid_down_mw_h", "afrr_committed_days",
                              "afrr_commit_failures", "afrr_replan_failures",
                              "afrr_commit_solve_time_mean_s", "afrr_commit_solve_time_max_s"))
    times = np.asarray(r["afrr_commit_solve_time_s"], dtype=float)
    return {"afrr_bid_up_mw_h": float(np.sum(r["afrr_bid_up"]) * dt),
            "afrr_bid_down_mw_h": float(np.sum(r["afrr_bid_down"]) * dt),
            "afrr_committed_days": len(times),
            "afrr_commit_failures": int(r["afrr_commit_failures"]),
            "afrr_replan_failures": int(r["afrr_replan_failures"]),
            "afrr_commit_solve_time_mean_s": float(np.mean(times)) if len(times) else None,
            "afrr_commit_solve_time_max_s": float(np.max(times)) if len(times) else None}


def afrr_price_columns(prices: CapacityPrices) -> dict:
    """A window's aFRR capacity prices as columns, one value each for the whole window: per
    direction the mean and the median, over the window's products, of the realised German
    average (EUR/MW/h), then per direction how often the bid rule is accepted. That is the
    share of the window's products after its first day, the ones with a bid price (the same
    product's German average of the day before), whose bid price is at or below the realised
    marginal, as `reserve.paid_prices` accepts it. It describes the rule and the prices, not
    what a rung offered: it is the same whatever MW B3 bid, none included, and in a run
    without B3. Empty on a window of one day, which has no product with a bid price.
    Block-level prices stay out of the tables, as the repository commits none.

    prices: the window's CapacityPrices (`afrr_data.capacity_prices`).
    """
    first = np.unique(prices.block, return_index=True)[1]
    average = {name: getattr(prices, f"average_{name}")[first] for name in ("up", "down")}
    cols = {f"afrr_{name}_price_{stat}_eur_mw_h": float(fn(average[name]))
            for stat, fn in (("mean", np.mean), ("median", np.median))
            for name in ("up", "down")}
    for name in ("up", "down"):
        bid = getattr(prices, f"bid_{name}")[first]
        marginal = getattr(prices, f"marginal_{name}")[first]
        priced = np.isfinite(bid)
        cols[f"afrr_{name}_bid_accepted_share"] = (
            float(np.mean(bid[priced] <= marginal[priced])) if priced.any() else None)
    return cols


def window_stats(label: str, wind: np.ndarray, pv: np.ndarray, price: np.ndarray,
                 run: RunConfig, window: dict) -> dict:
    """What the README's statements about a window rest on, for one plant.

    label: the window's name, e.g. "2024". wind, pv: the plant's available generation per
    step, MW. price: day-ahead price per step, EUR/MWh.
    run: the plant, its marketing fee and the step length. window: `window_columns(...)`.
    Returns {"window", the window columns, the mean price, the quarter-hours with a negative
    price and with a price below the marketing fee (negative ones included, as exporting
    there without a premium loses money), the peak generation, the largest combined capacity
    factor (peak over MW wind plus MWp PV), the connection, the steps whose generation
    exceeds it and whether it binds at all}.
    """
    gen = np.asarray(wind) + np.asarray(pv)
    price = np.asarray(price)
    over = gen > run.plant.conn_mw
    fee = run.market.direct_marketing_fee
    per_step = run.dt / 0.25                      # quarter-hours in a step
    return {"window": label, **window,
            "mean_price_eur_mwh": float(np.mean(price)),
            "negative_price_quarter_hours": float(np.sum(price < 0) * per_step),
            "below_fee_quarter_hours": float(np.sum(price < fee) * per_step),
            "fee_eur_mwh": fee,
            "installed_mw": run.plant.installed_mw,
            "peak_generation_mw": float(gen.max()),
            "max_combined_cf": float(gen.max() / run.plant.installed_mw),
            "connection_mw": run.plant.conn_mw,
            "steps_over_connection": int(over.sum()),
            "connection_binds": bool(over.any())}


def write_csv(rows: list[dict], path: str | Path) -> Path:
    """Write tidy rows to a CSV at `path`, creating its folder. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # one line ending on every system, so that a rerun elsewhere diffs only where numbers moved
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")
    return path


def merge_csv(path: Path, rows: list[dict], key: str) -> None:
    """Put `rows` into the CSV at `path`: they replace the rows that share their `key`, the
    others stay, and the file is sorted by `key`, so its order does not depend on the order
    the rows came in. A lock lets processes that run side by side merge into one file.

    path: the CSV, created with its folder if missing. rows: the new rows, as dicts.
    key: the column that names what a row belongs to, e.g. "experiment".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(rows)
    with _lock(path):
        if path.exists():
            # read as text, so the rows that stay are written back exactly as they were
            old = pd.read_csv(path, dtype=str, keep_default_na=False)
            new = pd.concat([old[~old[key].isin(new[key].astype(str))], new],
                            ignore_index=True)
        partial = path.with_name(f"{path.name}.partial")
        new.sort_values(key, kind="stable").to_csv(partial, index=False, lineterminator="\n")
        # one step, so no reader sees half a file
        _waiting_out_refusals(lambda: os.replace(partial, path))


def _waiting_out_refusals(action, timeout_s: float = 30.0):
    """Run `action`, a file operation, and repeat it while it fails with PermissionError, for
    up to `timeout_s` seconds. Windows refuses to replace or delete a file that another
    program holds open, and a virus scanner opens each new file for a moment; four processes
    side by side lost a quarter of their rows to such refusals.

    action: e.g. lambda: os.replace(partial, path). Returns what it returns.
    Raises its last PermissionError once `timeout_s` has passed, e.g. while a viewer keeps
    the file open.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


@contextlib.contextmanager
def _lock(path: Path, timeout_s: float = 300.0):
    """Hold a lock on `path` across processes: a file in the temp folder that only one
    process can create at a time.

    Raises TimeoutError when another process has held it for `timeout_s` seconds.
    """
    # in the temp folder, so that a lock left by a crash never lands among the tables
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    lock = Path(tempfile.gettempdir()) / f"hybrid-table-{digest}.lock"
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError) as err:
            # Held by another process. Windows refuses a lock file that its holder is
            # deleting with PermissionError rather than FileExistsError, for a moment.
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} has been held for {timeout_s:.0f} s ({err}); if "
                                   f"no process is writing {path}, delete it") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        _waiting_out_refusals(lambda: os.remove(lock))

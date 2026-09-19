"""Robustness: the rolling-horizon evaluation for every measured household.

Runs B1, B2 and the deployable B3 (`MAIN_VARIANTS`) for each HTW profile that passes the PV
screen, each scaled to the thesis consumption (3221 kWh/a) so that only the shape and
variability of the load differ. One row per household is appended to the output CSV as soon
as it is done, so an interrupted run resumes where it stopped.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from ..data.dataset import load_dataset, with_measured_load
from .rolling_eval import MAIN_VARIANTS, run_rolling_eval


def _one(household: str, dataset_path: str, htw_path: str) -> dict:
    df = with_measured_load(load_dataset(dataset_path), pd.read_parquet(htw_path), household)
    s = run_rolling_eval(df, variants=MAIN_VARIANTS, progress=False)["summary"]
    b3 = MAIN_VARIANTS[0].name
    return {
        "household": household,
        "peak_kw": float(df["load_kw"].max()),
        "cost_b1": s.loc["B1 rule-based", "net cost EUR"],
        "cost_b2": s.loc["B2 perfect foresight", "net cost EUR"],
        "cost_b3": s.loc[b3, "net cost EUR"],
        "capture_b3_pct": s.loc[b3, "capture of B1->B2 %"],
        "violations_b3": s.loc[b3, "violations"],
    }


def run_household_sweep(households, dataset_path: str, htw_path: str, out_csv: str,
                        workers: int = 4) -> pd.DataFrame:
    out = Path(out_csv)
    done = set(pd.read_csv(out)["household"]) if out.exists() else set()
    todo = [h for h in households if h not in done]
    print(f"{len(done)} done, {len(todo)} to run with {workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, h, dataset_path, htw_path): h for h in todo}
        for fut in as_completed(futures):
            row = fut.result()
            pd.DataFrame([row]).to_csv(out, mode="a", header=not out.exists(), index=False)
            print(f"  {row['household']}: B3 captures {row['capture_b3_pct']:.1f} %", flush=True)
    return pd.read_csv(out)

"""Robustness: the rolling-horizon evaluation for every measured household.

Runs B1, B2 and the deployable B3 (`MAIN_VARIANTS`) for each HTW profile that passes the PV
screen, each scaled to the thesis consumption (3221 kWh/a) so that only the shape and
variability of the load differ. One row per household is appended to the output CSV as soon
as it is done, so an interrupted run resumes where it stopped.

Each row carries a fingerprint of the two input files, and a resume is refused when the CSV
was written from other inputs. Without it, a rerun after a data fix found every household
already done, exited successfully and left the stale CSV untouched. Code changes are not
fingerprinted: after changing a controller, move the CSV away before rerunning.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from ..data.dataset import load_dataset, with_measured_load
from .rolling_eval import MAIN_VARIANTS, run_rolling_eval

FINGERPRINT = "inputs_sha256"


def _input_fingerprint(*paths: str) -> str:
    """First 16 hex digits of a SHA-256 over the bytes of every input file, in order."""
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:16]


def _done_households(out: Path, fingerprint: str) -> set[str]:
    """Households already in `out`, provided every row was written from the same inputs."""
    if not out.exists():
        return set()
    prev = pd.read_csv(out, dtype=str)
    if FINGERPRINT not in prev or (prev[FINGERPRINT] != fingerprint).any():
        raise ValueError(f"{out} was not written from these inputs (dataset and HTW file); "
                         "move or delete it to rerun the sweep from scratch")
    return set(prev["household"])


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
    fingerprint = _input_fingerprint(dataset_path, htw_path)
    done = _done_households(out, fingerprint)
    todo = [h for h in households if h not in done]
    if workers <= 1:
        for h in todo:
            row = {**_one(h, dataset_path, htw_path), FINGERPRINT: fingerprint}
            pd.DataFrame([row]).to_csv(out, mode="a", header=not out.exists(), index=False)
            print(f"  {row['household']}: B3 captures {row['capture_b3_pct']:.1f} %", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, h, dataset_path, htw_path): h for h in todo}
            for fut in as_completed(futures):
                row = {**fut.result(), FINGERPRINT: fingerprint}
                pd.DataFrame([row]).to_csv(out, mode="a", header=not out.exists(), index=False)
                print(f"  {row['household']}: B3 captures {row['capture_b3_pct']:.1f} %", flush=True)
    return pd.read_csv(out)

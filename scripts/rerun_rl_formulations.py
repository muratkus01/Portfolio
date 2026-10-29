"""Rerun all four SAC formulations on the post-clock-fix HTW dataset.

Why: `htw.replay_on_calendar` read the HTW labels as MEZ when they follow legal local time, so
every result with an HTW household as truth replayed its summer load an hour late. B0 to B3,
the capture ladder, H31, the quarter-hour study and the forecast nRMSE were rerun on the
company laptop; the RL results were not, so the headline table currently compares RL at 27.0 %
against a B3 of 89.4 % measured on a different dataset.

Writes to a staging directory and does NOT touch `reports/` on its own. Nothing is published
until all four formulations finish and their numbers have been looked at. A half-finished
rerun mixed into the committed reports would be worse than the stale numbers it replaces.

The four formulations and the report directory each one belongs to:

    clip    + net_cost       rl_ablation_clip_net_cost       the original, -5.9 % pre-fix
    rescale + net_cost       rl_ablation_rescale_net_cost    action rescaling only, -9.9 %
    clip    + differential   rl_ablation_clip_differential   differential reward only, +22.7 %
    rescale + differential   rl_eval_H28                     both, the headline, +27.0 %

Everything else is left at the published defaults, 500,000 steps and 3 seeds, because changing
them would make the pre- and post-fix numbers incomparable, which is the whole point.

    python scripts/rerun_rl_formulations.py
    python scripts/rerun_rl_formulations.py --only rescale_differential
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P01 = ROOT / "projects" / "01-prosumer-pv-bess-mpc-rl"
STAGE = P01 / "data" / "processed" / "rl_rerun_stage"
DATA = "data/processed/site_2024_2026_htw_H28.parquet"
B3 = "reports/rolling_eval_htw_H28/rolling_eval_monthly_cost.csv"

FORMULATIONS = [
    ("clip_net_cost", "clip", "net_cost", "rl_ablation_clip_net_cost"),
    ("rescale_net_cost", "rescale", "net_cost", "rl_ablation_rescale_net_cost"),
    ("clip_differential", "clip", "differential", "rl_ablation_clip_differential"),
    ("rescale_differential", "rescale", "differential", "rl_eval_H28"),
]


def run_one(key: str, action: str, reward: str, workers: int, steps: int, seeds: int) -> bool:
    out = STAGE / key
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "prosumer.cli", "rl-eval",
           "--data", DATA, "--b3", B3,
           "--out", str(out.relative_to(P01)).replace("\\", "/"),
           "--action-mode", action, "--reward-mode", reward,
           "--steps", str(steps), "--seeds", str(seeds), "--workers", str(workers),
           "--model-dir", f"data/processed/rl_models_rerun/{key}"]
    print(f"\n{'=' * 78}\n{key}: action={action} reward={reward}\n{' '.join(cmd)}\n{'=' * 78}",
          flush=True)
    t0 = time.time()
    env = {"PYTHONPATH": "src"}
    import os
    proc = subprocess.run(cmd, cwd=P01, env={**os.environ, **env})
    mins = (time.time() - t0) / 60.0
    ok = proc.returncode == 0
    print(f"\n{key}: {'done' if ok else 'FAILED rc=' + str(proc.returncode)} in {mins:.1f} min",
          flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=[f[0] for f in FORMULATIONS])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--steps", type=int, default=500_000)
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()

    if not (P01 / DATA).exists():
        print(f"missing {DATA}; rebuild it before rerunning RL")
        return 1

    todo = [f for f in FORMULATIONS if args.only is None or f[0] == args.only]
    print(f"staging to {STAGE}")
    print(f"{len(todo)} formulation(s), {args.seeds} seeds, {args.steps:,} steps, "
          f"{args.workers} workers")

    t0 = time.time()
    failed = []
    for key, action, reward, _ in todo:
        if not run_one(key, action, reward, args.workers, args.steps, args.seeds):
            failed.append(key)

    print(f"\n{'=' * 78}")
    print(f"total {(time.time() - t0) / 60.0:.1f} min")
    if failed:
        print("FAILED: " + ", ".join(failed))
        print("Nothing was copied into reports/. Fix and rerun the failures.")
        return 1
    print("All formulations finished. Staged results:")
    for key, _, _, dest in todo:
        s = STAGE / key / "rl_summary.csv"
        print(f"  {key:22s} -> {s}  (publish to reports/{dest}/)")
    print("\nNothing has been copied into reports/ yet. Review the numbers first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

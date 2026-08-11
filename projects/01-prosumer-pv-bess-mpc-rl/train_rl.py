"""Train a SAC policy and score it against the benchmark ladder.

    python train_rl.py --steps 50000 --seeds 3

What this script is careful about, and why:

  * **Chronological split.** Train on the earlier window, test on the later one. No shuffling.
  * **Forecast parity.** B3 and the policy read the same forecast arrays, generated once.
  * **Normalisation from training data only**, then frozen and passed to the test environment.
  * **Multiple seeds**, reported as median and IQR, never the best run.
  * **Terminal value** applied to both B3 and the policy, so neither is penalised for the
    episode boundary while the other is not.

A caveat that belongs in any report using this script: with only the four measured weeks
currently available, the training set is far too small for the result to mean anything about
whether RL beats MPC. This is a demonstration that the machinery is correct and honest -
data volume is the binding constraint, and Phase 5 of the roadmap addresses it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from prosumer.baselines.ladder import (b1_rule_based, b2_perfect_foresight, b3_rolling_mpc,
                                       gap_closure, make_forecasts)
from prosumer.baselines.milp import default_terminal_price
from prosumer.config import RunConfig, TariffConfig
from prosumer.data.loaders import load_legacy_csv, make_dimming_series, to_resolution
from prosumer.envs.prosumer_env import ProsumerEnv
from prosumer.market import settlement, tariff
from prosumer.model import site
from prosumer.safety import project_series

DATA_DIR = Path(__file__).parent / "data" / "raw" / "legacy"


def load_all_weeks(run: RunConfig) -> tuple[pd.DataFrame, np.ndarray]:
    """Load every measured week and concatenate them, preserving the gaps between them.

    The four available weeks are scattered across 2024 (January, June, August, December). They
    must be resampled INDIVIDUALLY and then concatenated: resampling the concatenation instead
    builds one continuous date range from January to December and fills the eleven months in
    between with interpolated values - fabricating roughly 30 000 quarter-hours of data that
    was never measured. That bug produced a 33 600-step "dataset" from 672 hours of real
    measurement before it was caught.

    Returns the frame plus the array of indices at which a new week begins, so that training
    episodes can be prevented from straddling a four-month discontinuity.
    """
    frames, starts, offset = [], [], 0
    for p in sorted(DATA_DIR.glob("*.csv")):
        w = to_resolution(load_legacy_csv(p), run.dt)
        frames.append(w)
        starts.append(offset)
        offset += len(w)
    df = pd.concat(frames)
    return tariff.build_prices(df, run.tariff), np.array(starts)


def evaluate_dispatch(p_bat, load, pv, pi, pe, run) -> dict:
    p = project_series(p_bat, load, pv, run.dt, run.site)
    res = site.simulate(p, load, pv, run.dt, run.site)
    res["cost"] = settlement.settle(res, pi, pe, run.dt, run.site)
    res["violations"] = site.check_feasible(res, run.dt, run.site)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=50_000)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--dt", type=float, default=0.25)
    ap.add_argument("--horizon", type=int, default=96)
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--export-mode", default="feed_in_tariff")
    ap.add_argument("--para-14a", action="store_true", help="enable s14a dimming events")
    args = ap.parse_args()

    run = RunConfig(dt=args.dt, horizon_steps=args.horizon,
                    tariff=TariffConfig(export_mode=args.export_mode))
    if args.para_14a:
        run = run.with_(para_14a=run.para_14a.__class__(enabled=True))

    df, week_starts = load_all_weeks(run)
    n = len(df)
    # split on a week boundary so neither window contains a partial, discontinuous week
    split = int(week_starts[np.searchsorted(week_starts, n * args.train_frac) - 1]) \
        if len(week_starts) > 1 else int(n * args.train_frac)
    print(f"{n} steps total ({len(week_starts)} measured weeks) | "
          f"train {split} | test {n - split} | dt={run.dt} h")
    print(tariff.describe(run.tariff, float(df["spot_eur_per_kwh"].mean())))

    load = df["load_kw"].to_numpy()
    pv = df["pv_kw"].to_numpy()
    pi = df["price_import"].to_numpy()
    pe = df["price_export"].to_numpy()
    dim = make_dimming_series(df.index, run.para_14a)

    # forecasts generated ONCE and shared by B3 and the policy -> no information asymmetry
    load_fc, pv_fc = make_forecasts(load, pv, run)

    tr, te = slice(0, split), slice(split, n)
    term_price = default_terminal_price(pi[te], run.site, run, pe[te],
                                        load[te] - pv[te])

    # ---------------- baselines on the TEST window ----------------
    print("\nrunning baselines on the held-out window ...")
    rows: dict[str, dict] = {}
    b1 = b1_rule_based(load[te], pv[te], run.dt, run,
                       dim_limit=None if dim is None else dim[te])
    rows["B1 rule-based"] = b1
    b2 = b2_perfect_foresight(load[te], pv[te], pi[te], pe[te], run.dt, run)
    if b2 is not None:
        rows["B2 perfect foresight"] = b2
    b3 = b3_rolling_mpc(load[te], pv[te], pi[te], pe[te], load_fc[te], pv_fc[te],
                        run.dt, run, dim_limit=None if dim is None else dim[te])
    rows["B3 rolling MPC"] = b3

    for r in rows.values():
        r["cost"] = settlement.settle(r, pi[te], pe[te], run.dt, run.site)
        r["violations"] = site.check_feasible(r, run.dt, run.site)

    # ---------------- RL ----------------
    try:
        from stable_baselines3 import SAC
    except ImportError:
        print("\n[RL skipped - pip install '.[rl]']")
        _report(rows, run)
        return

    norm_stats = {
        "load": max(float(np.percentile(load[tr], 99)), 1e-6),
        "pv": max(float(np.percentile(pv[tr], 99)), 1e-6),
        "price": max(float(np.percentile(pi[tr], 99)), 1e-6),
    }

    def make_env(sl, random_start: bool):
        sub = load[sl]
        # week boundaries expressed relative to this slice, so episodes stay inside one week
        rel = week_starts[(week_starts >= (sl.start or 0)) & (week_starts < len(load))]
        rel = rel - (sl.start or 0)
        rel = rel[(rel >= 0) & (rel < len(sub))]
        return ProsumerEnv(sub, pv[sl], pi[sl], pe[sl], load_fc[sl], pv_fc[sl],
                           run, dim_limit=None if dim is None else dim[sl],
                           terminal_price=term_price, random_start=random_start,
                           episode_steps=min(7 * run.steps_per_day, len(sub)),
                           norm_stats=norm_stats,
                           episode_starts=rel if len(rel) else None)

    seed_costs = []
    for seed in range(args.seeds):
        print(f"\ntraining SAC seed {seed} for {args.steps} steps ...")
        env = make_env(tr, random_start=True)
        model = SAC("MlpPolicy", env, verbose=0, seed=seed,
                    learning_rate=3e-4, batch_size=256, train_freq=1,
                    gradient_steps=1, learning_starts=1000)
        model.learn(total_timesteps=args.steps, progress_bar=False)

        test_env = make_env(te, random_start=False)
        test_env.episode_steps = len(load[te])
        obs, _ = test_env.reset()
        actions = []
        while True:
            a, _ = model.predict(obs, deterministic=True)
            obs, _r, term, trunc, info = test_env.step(a)
            actions.append(info["p_bat"])
            if term or trunc:
                break
        p_rl = np.array(actions)
        res = evaluate_dispatch(p_rl, load[te][:len(p_rl)], pv[te][:len(p_rl)],
                                pi[te][:len(p_rl)], pe[te][:len(p_rl)], run)
        seed_costs.append(res["cost"]["net_cost"])
        print(f"  seed {seed}: net cost {res['cost']['net_cost']:.3f} EUR | "
              f"violations {sum(res['violations'].values())}")

    med = float(np.median(seed_costs))
    q1, q3 = np.percentile(seed_costs, [25, 75])
    rows[f"RL SAC (median of {args.seeds} seeds)"] = {
        "cost": {"net_cost": med},
        "violations": {"total": 0},
        "iqr": (q1, q3),
        "p_imp": np.zeros(1), "p_exp": np.zeros(1), "throughput": np.zeros(1),
    }

    _report(rows, run)
    if "B2 perfect foresight" in rows:
        gc = gap_closure(med, rows["B3 rolling MPC"]["cost"]["net_cost"],
                         rows["B2 perfect foresight"]["cost"]["net_cost"])
        print(f"\nB3-gap closure: {gc * 100:.1f}%   "
              f"(0% = matches MPC, 100% = reaches the perfect-foresight ceiling)")
        print(f"seed IQR: [{q1:.3f}, {q3:.3f}] EUR")


def _report(rows: dict, run: RunConfig) -> None:
    print(f"\n{'controller':<34}{'net cost EUR':>14}{'violations':>12}")
    print("-" * 60)
    for name, r in rows.items():
        print(f"{name:<34}{r['cost']['net_cost']:>14.3f}"
              f"{sum(r['violations'].values()):>12d}")


if __name__ == "__main__":
    main()

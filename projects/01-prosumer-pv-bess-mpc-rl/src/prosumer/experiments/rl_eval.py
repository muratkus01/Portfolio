"""Experiment: does a SAC policy beat the realistic B3?

Same protocol as `rolling_eval`, so the numbers sit in one table:
  train  2024 (random one-week episodes), normalisation statistics and terminal values
         from 2024 only
  test   2025-01-01 to the end of the data, one continuous deterministic rollout
  info   the policy observes exactly what B3 uses: published prices only, the day-ahead PV
         forecast and the standard-profile load forecast (`RealisticProsumerEnv`)

Several seeds are trained in parallel and reported as median and range, never the best run.
The benchmark RL has to clear is B3 (the deployable classical controller), not B2.
"""
from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from ..baselines.ladder import b1_rule_based
from ..baselines.lp_fast import solve_window_fast
from ..baselines.rolling import InformationModel, fit_terminal_values, forecast_arrays
from ..model import site
from ..scenarios import EXTENSION_RUN
from .rolling_eval import _prices, _step_costs

LOCAL_TZ = "Europe/Berlin"


def _split(df, train=("2024-01-01", "2025-01-01"), test_start="2025-01-01"):
    t0, t1, ts = (pd.Timestamp(x).tz_localize(LOCAL_TZ) for x in (*train, test_start))
    return df[(df.index >= t0) & (df.index < t1)], df[df.index >= ts]


def _make_env(part: pd.DataFrame, run, tv, stats, random_start: bool, episode_steps=None,
              reward_mode: str = "differential", action_mode: str = "rescale"):
    from ..envs.realistic_env import RealisticProsumerEnv
    pi, pe = _prices(part, run)
    pv_fc, load_fc = forecast_arrays(part, InformationModel(), run.steps_per_day)
    return RealisticProsumerEnv(
        part.index, part["load_kw"].to_numpy(), part["pv_kw"].to_numpy(), pi, pe,
        load_fc, pv_fc, run, terminal_values=tv, random_start=random_start,
        episode_steps=episode_steps, norm_stats=stats,
        reward_mode=reward_mode, action_mode=action_mode)


def _train_one(seed: int, dataset_path: str, steps: int, model_dir: str,
               reward_mode: str = "differential", action_mode: str = "rescale") -> dict:
    import torch
    from stable_baselines3 import SAC
    torch.set_num_threads(2)

    run = EXTENSION_RUN
    df = pd.read_parquet(dataset_path)
    tr, te = _split(df)
    tv = fit_terminal_values(tr, *_prices(tr, run), run)
    pi_tr, _ = _prices(tr, run)
    stats = {"load": float(np.percentile(tr["load_kw"], 99)),
             "pv": float(np.percentile(tr["pv_kw"], 99)),
             "price": float(np.percentile(pi_tr, 99))}

    env = _make_env(tr, run, tv, stats, random_start=True, episode_steps=7 * 96,
                    reward_mode=reward_mode, action_mode=action_mode)
    model = SAC("MlpPolicy", env, seed=seed, verbose=0, gamma=0.995, learning_rate=3e-4,
                batch_size=256, buffer_size=1_000_000, learning_starts=10_000,
                train_freq=1, gradient_steps=1, policy_kwargs={"net_arch": [256, 256]})
    t0 = time.perf_counter()
    print(f"[Seed {seed}] Starting training for {steps} steps...", flush=True)
    model.learn(total_timesteps=steps, progress_bar=False)
    train_s = time.perf_counter() - t0
    print(f"[Seed {seed}] Training completed in {train_s / 60:.1f} min. Saving model...", flush=True)
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    model.save(Path(model_dir) / f"sac_seed{seed}")

    print(f"[Seed {seed}] Evaluating on out-of-sample test period ({len(te)} steps)...", flush=True)
    test_env = _make_env(te, run, tv, stats, random_start=False, episode_steps=len(te),
                         reward_mode=reward_mode, action_mode=action_mode)
    obs, _ = test_env.reset()
    p_bat, clipped = [], 0
    while True:
        a, _ = model.predict(obs, deterministic=True)
        obs, _r, term, trunc, info = test_env.step(a)
        p_bat.append(info["p_bat"])
        clipped += int(info["clipped"])
        if term or trunc:
            break
    load, pv = te["load_kw"].to_numpy(), te["pv_kw"].to_numpy()
    res = site.simulate(np.array(p_bat), load, pv, run.dt, run.site)
    pi, pe = _prices(te, run)
    print(f"[Seed {seed}] Evaluation complete.", flush=True)
    return {"seed": seed, "step_costs": _step_costs(res, pi, pe, run),
            "cycles": res["p_dis"].sum() * run.dt / run.site.usable_kwh,
            "violations": sum(site.check_feasible(res, run.dt, run.site).values()),
            "clipped_share": clipped / len(p_bat), "train_minutes": train_s / 60,
            "reward_mode": reward_mode, "action_mode": action_mode}


def run_rl_eval(dataset_path: str, steps: int = 500_000, seeds: int = 3, workers: int = 3,
                b3_monthly_csv: str | None = None, out_dir: str | Path | None = None,
                model_dir: str = "data/processed/rl_models",
                reward_mode: str = "differential",
                action_mode: str = "rescale") -> dict[str, pd.DataFrame]:
    run = EXTENSION_RUN
    df = pd.read_parquet(dataset_path)
    _, te = _split(df)
    pi, pe = _prices(te, run)
    load, pv = te["load_kw"].to_numpy(), te["pv_kw"].to_numpy()

    costs = {"B1 rule-based": _step_costs(b1_rule_based(load, pv, run.dt, run), pi, pe, run)}
    b2 = solve_window_fast(load, pv, pi, pe, run.dt, run.site, run.site.soc_init)
    costs["B2 perfect foresight"] = _step_costs(
        site.simulate(b2["p_bat"], load, pv, run.dt, run.site), pi, pe, run)

    print(f"Launching RL evaluation: {seeds} seeds, {steps} steps each, {workers} parallel workers...", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_train_one, range(seeds), [dataset_path] * seeds,
                                [steps] * seeds, [model_dir] * seeds,
                                [reward_mode] * seeds, [action_mode] * seeds))
    for r in results:
        costs[f"RL SAC seed {r['seed']}"] = r["step_costs"]

    month = te.index.tz_convert(LOCAL_TZ).strftime("%Y-%m")
    monthly = pd.DataFrame(costs, index=te.index).groupby(month).sum()
    if b3_monthly_csv and Path(b3_monthly_csv).exists():
        b3 = pd.read_csv(b3_monthly_csv, index_col=0)
        monthly["B3 NWP PV, SLP load, fitted TV"] = b3["B3 NWP PV, SLP load, fitted TV"]

    tot = monthly.sum()
    head = tot["B1 rule-based"] - tot["B2 perfect foresight"]
    summary = pd.DataFrame({"net cost EUR": tot,
                            "capture of B1->B2 %": 100 * (tot["B1 rule-based"] - tot) / head})
    seeds_cap = summary.loc[[c for c in summary.index if c.startswith("RL SAC")],
                            "capture of B1->B2 %"]
    summary.attrs["rl_median_capture"] = float(seeds_cap.median())
    detail = pd.DataFrame([{k: v for k, v in r.items() if k != "step_costs"} for r in results])

    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        monthly.round(4).to_csv(out / "rl_monthly_cost.csv")
        summary.round(4).to_csv(out / "rl_summary.csv")
        detail.round(4).to_csv(out / "rl_seeds.csv", index=False)
    return {"monthly": monthly, "summary": summary, "seeds": detail}

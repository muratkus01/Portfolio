"""Train and evaluate PPO policy on Project 02 Pumped Storage Multi-Market environment.

Includes action smoothing, wear penalty shaping, and multi-timescale capacity ablation.
"""
import argparse
import time
import numpy as np
import pandas as pd
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "datakit") not in sys.path:
    sys.path.insert(0, str(ROOT / "datakit"))
if str(ROOT / "projects" / "02-pumped-storage-rl-multimarket" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "projects" / "02-pumped-storage-rl-multimarket" / "src"))

import datakit
from psw.config import RunConfig, PlantConfig, MarketConfig
from psw.env import PSWEnv
from psw.baselines import run_ladder
from psw.market import expand_blocks, activation_series, rebap_series, settle
from psw.plant import check_feasible, security_readiness, reversals

import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

def build_capacity_bids(n: int, run: RunConfig, fraction: float = 0.15) -> tuple:
    bs = run.market.block_steps
    nb = int(np.ceil(n / bs))
    pos = np.full(nb, run.plant.p_turb_max * fraction)
    neg = np.full(nb, run.plant.p_pump_max * fraction)
    return expand_blocks(pos, n, bs), expand_blocks(neg, n, bs)

class ShapedPSWEnv(PSWEnv):
    """PSWEnv with wear penalty shaping and action smoothing."""
    def __init__(self, *args, wear_weight: float = 3.0, action_smooth: float = 0.5, **kwargs):
        super().__init__(*args, **kwargs)
        self.wear_weight = wear_weight
        self.action_smooth = action_smooth
        self.prev_action_0 = 0.0

    def reset(self, **kwargs):
        self.prev_action_0 = 0.0
        return super().reset(**kwargs)

    def step(self, action):
        a = np.copy(action)
        if self.action_smooth > 0:
            a[0] = (1 - self.action_smooth) * a[0] + self.action_smooth * self.prev_action_0
            self.prev_action_0 = a[0]

        obs, reward, term, trunc, info = super().step(a)

        # Apply shaped wear penalty during training so the policy internalises rotor reversal costs
        p = info["p"]
        cur_mode = 1 if p > 1e-6 else (-1 if p < -1e-6 else 0)
        mode_changed = (cur_mode != 0) and (self.last_mode != 0) and (cur_mode != self.last_mode)
        if mode_changed and self.wear_weight > 1.0:
            extra_wear = (self.wear_weight - 1.0) * self.cfg.mode_change_cost
            reward -= extra_wear * self.reward_scale

        return obs, reward, term, trunc, info

def run_experiment(year=2025, train_days=90, eval_days=10, total_timesteps=60_000,
                   wear_weight=3.0, action_smooth=0.5, seed=42):
    print(f"Loading {year} DE-LU prices for training ({train_days} days) and eval ({eval_days} days)...", flush=True)
    df = datakit.day_ahead_price(f"{year}-01-01", f"{year + 1}-01-01")
    s = df["price_eur_per_mwh"]

    dt = 0.25
    steps_train = int(train_days * 24 / dt)
    target_train = pd.date_range(s.index[0], periods=steps_train, freq="15min", tz="UTC")
    price_train = s.reindex(s.index.union(target_train)).ffill().reindex(target_train).to_numpy()

    steps_eval = int(eval_days * 24 / dt)
    target_eval = pd.date_range(s.index[0], periods=steps_eval, freq="15min", tz="UTC")
    price_eval = s.reindex(s.index.union(target_eval)).ffill().reindex(target_eval).to_numpy()

    run = RunConfig(dt=dt, seed=seed)

    # 1. Official Baselines on the 10-day evaluation window
    print("Computing B1, B2, B3 baselines on 10-day evaluation window...", flush=True)
    rng_eval = np.random.default_rng(run.seed)
    sp_eval, sn_eval = build_capacity_bids(steps_eval, run)
    act_eval = activation_series(steps_eval, sp_eval, sn_eval, run.market, rng_eval)
    reb_eval = rebap_series(steps_eval, price_eval, run.market, rng_eval)

    ladder_res = run_ladder(price_eval, run, sp_eval, sn_eval, act_eval, reb_eval, include_b3=True)

    # 2. Setup training environment with wear shaping and action smoothing
    def make_train_env():
        return ShapedPSWEnv(price_train, price_train, run, episode_steps=96 * 7,
                            random_start=True, seed=seed,
                            wear_weight=wear_weight, action_smooth=action_smooth)

    env = DummyVecEnv([make_train_env])

    print(f"Training Shaped PPO (wear_weight={wear_weight}, action_smooth={action_smooth}) for {total_timesteps:,} steps...", flush=True)
    t0 = time.time()
    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.005,
        verbose=0,
        seed=seed,
    )
    model.learn(total_timesteps=total_timesteps)
    train_time = time.time() - t0
    print(f"Training complete in {train_time:.1f}s ({total_timesteps/train_time:.0f} steps/s)", flush=True)

    # 3. RL Policy Evaluation on the 10 days
    print("Evaluating RL policies on 10-day window across multi-timescale configurations...", flush=True)

    def evaluate_policy(eval_wear_weight=1.0, eval_smooth=0.5, cap_mode="symmetric"):
        eval_env = ShapedPSWEnv(price_eval, price_eval, run, rebap=reb_eval,
                                episode_steps=steps_eval, random_start=False, seed=seed,
                                wear_weight=eval_wear_weight, action_smooth=eval_smooth)
        obs, _ = eval_env.reset()
        p_trace, ps_trace, e_trace, sp_trace, sn_trace, act_trace = [], [], [], [], [], []

        for t in range(steps_eval):
            action, _ = model.predict(obs, deterministic=True)
            if cap_mode == "symmetric":
                action[1] = 0.15
                action[2] = 0.15
            elif cap_mode == "unidirectional":
                action[1] = 0.15
                action[2] = 0.0
            obs, reward, term, trunc, info = eval_env.step(action)
            p_trace.append(info["p"])
            ps_trace.append(info["p_sched"])
            e_trace.append(info["e_res"])
            sp_trace.append(info["sold_pos"])
            sn_trace.append(info["sold_neg"])
            act_trace.append(info["activation"])
            if term or trunc:
                break

        p_arr = np.array(p_trace)
        res = {
            "p": p_arr, "p_sched": np.array(ps_trace),
            "p_turb": np.maximum(p_arr, 0.0), "p_pump": np.maximum(-p_arr, 0.0),
            "e_res": np.array(e_trace), "mode_changes": reversals(p_arr),
        }
        cost = settle(res, price_eval, dt, run.plant, run.market,
                      np.array(sp_trace), np.array(sn_trace), np.array(act_trace),
                      schedule=res["p_sched"], rebap=reb_eval)
        v = check_feasible(res, run.plant)
        sec = security_readiness(res, run.plant, dt)["index"]
        return cost, v, sec

    cost_shaped, v_shaped, sec_shaped = evaluate_policy(eval_smooth=action_smooth, cap_mode="symmetric")
    cost_unidir, v_unidir, sec_unidir = evaluate_policy(eval_smooth=action_smooth, cap_mode="unidirectional")

    b1_c = ladder_res["B1 price threshold"]["cost"]
    b2_c = ladder_res["B2 perfect foresight"]["cost"]
    b3_c = ladder_res["B3 rolling MPC"]["cost"]
    headroom = b2_c["net_revenue"] - b1_c["net_revenue"]

    print("\nBenchmark Ladder & Multi-Timescale Ablation (10 Days 2025):", flush=True)
    hdr = f"{'controller':<28}{'net EUR':>12}{'energy':>11}{'capacity':>10}{'wear':>9}{'security':>10}{'viol.':>7}"
    print(hdr)
    print("-" * len(hdr))

    def print_row(name, c, sec, viol):
        print(f"{name:<28}{c['net_revenue']:>12,.0f}{c['energy_revenue'] - c['pump_cost']:>11,.0f}"
              f"{c['capacity_revenue']:>10,.0f}{c['wear_cost']:>9,.0f}{sec:>10.3f}{viol:>7d}")

    print_row("B1 price threshold", b1_c, ladder_res["B1 price threshold"]["security"]["index"], sum(ladder_res["B1 price threshold"]["violations"].values()))
    print_row("RL PPO (smoothed symmetric)", cost_shaped, sec_shaped, sum(v_shaped.values()))
    print_row("RL PPO (unidirectional cap)", cost_unidir, sec_unidir, sum(v_unidir.values()))
    print_row("B3 rolling MPC", b3_c, ladder_res["B3 rolling MPC"]["security"]["index"], sum(ladder_res["B3 rolling MPC"]["violations"].values()))
    print_row("B2 perfect foresight", b2_c, ladder_res["B2 perfect foresight"]["security"]["index"], sum(ladder_res["B2 perfect foresight"]["violations"].values()))

    print("-" * len(hdr))
    print(f"Headroom B2 - B1: {headroom:,.0f} EUR")
    print(f"B3 recovery above B1: {(b3_c['net_revenue'] - b1_c['net_revenue']) / headroom * 100:.1f}%")
    print(f"RL smoothed recovery above B1: {(cost_shaped['net_revenue'] - b1_c['net_revenue']) / headroom * 100:.1f}% ({cost_shaped['net_revenue'] - b1_c['net_revenue']:,.0f} EUR)")
    print(f"RL unidirectional cap recovery: {(cost_unidir['net_revenue'] - b1_c['net_revenue']) / headroom * 100:.1f}% ({cost_unidir['net_revenue'] - b1_c['net_revenue']:,.0f} EUR)")

    # Save model
    model_dir = Path("projects/02-pumped-storage-rl-multimarket/models")
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "ppo_psw_reference"
    model.save(model_path)
    print(f"Saved trained PPO model to: {model_path}.zip", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesteps", type=int, default=60_000)
    parser.add_argument("--wear-weight", type=float, default=3.0)
    parser.add_argument("--action-smooth", type=float, default=0.5)
    parser.add_argument("--train-days", type=int, default=90)
    parser.add_argument("--eval-days", type=int, default=10)
    args = parser.parse_args()
    run_experiment(train_days=args.train_days, eval_days=args.eval_days,
                   total_timesteps=args.timesteps, wear_weight=args.wear_weight,
                   action_smooth=args.action_smooth)

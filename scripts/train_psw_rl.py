"""Train and evaluate PPO policy on Project 02 Pumped Storage Multi-Market environment."""
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

def build_capacity_bids(n: int, run: RunConfig, fraction: float = 0.15) -> tuple:
    bs = run.market.block_steps
    nb = int(np.ceil(n / bs))
    pos = np.full(nb, run.plant.p_turb_max * fraction)
    neg = np.full(nb, run.plant.p_pump_max * fraction)
    return expand_blocks(pos, n, bs), expand_blocks(neg, n, bs)
from psw.plant import check_feasible, security_readiness, reversals

import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

def run_experiment(year=2025, train_days=90, eval_days=10, total_timesteps=100_000, seed=42):
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
    
    # 2. Setup training environment
    def make_train_env():
        return PSWEnv(price_train, price_train, run, episode_steps=96 * 7, random_start=True, seed=seed)
        
    env = DummyVecEnv([make_train_env])
    
    print(f"Training PPO for {total_timesteps:,} steps on {train_days} days of data...", flush=True)
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
    print("Evaluating RL policy on 10-day window...", flush=True)
    eval_env = PSWEnv(price_eval, price_eval, run, rebap=reb_eval, episode_steps=steps_eval, random_start=False, seed=seed)
    
    obs, _ = eval_env.reset()
    p_trace = []
    p_sched_trace = []
    e_trace = []
    sold_pos_trace = []
    sold_neg_trace = []
    act_trace = []
    
    for t in range(steps_eval):
        action, _ = model.predict(obs, deterministic=True)
        # Fix capacity action to 15% static to match B1/B2/B3 reservation for direct comparison
        action[1] = 0.15
        action[2] = 0.15
        obs, reward, terminated, truncated, info = eval_env.step(action)
        p_trace.append(info["p"])
        p_sched_trace.append(info["p_sched"])
        e_trace.append(info["e_res"])
        sold_pos_trace.append(info["sold_pos"])
        sold_neg_trace.append(info["sold_neg"])
        act_trace.append(info["activation"])
        if terminated or truncated:
            break
            
    p_arr = np.array(p_trace)
    res_rl = {
        "p": p_arr,
        "p_sched": np.array(p_sched_trace),
        "p_turb": np.maximum(p_arr, 0.0),
        "p_pump": np.maximum(-p_arr, 0.0),
        "e_res": np.array(e_trace),
        "mode_changes": reversals(p_arr),
    }
    
    c_rl = settle(res_rl, price_eval, dt, run.plant, run.market,
                  np.array(sold_pos_trace), np.array(sold_neg_trace),
                  np.array(act_trace), schedule=res_rl["p_sched"], rebap=reb_eval)
    v_rl = check_feasible(res_rl, run.plant)
    sec_rl = security_readiness(res_rl, run.plant, dt)["index"]
    
    b1_c = ladder_res["B1 price threshold"]["cost"]
    b2_c = ladder_res["B2 perfect foresight"]["cost"]
    b3_c = ladder_res["B3 rolling MPC"]["cost"]
    
    headroom = b2_c["net_revenue"] - b1_c["net_revenue"]
    rec_b3 = (b3_c["net_revenue"] - b1_c["net_revenue"]) / headroom * 100
    rec_rl = (c_rl["net_revenue"] - b1_c["net_revenue"]) / headroom * 100
    
    print("\nBenchmark Ladder Comparison (10 Days 2025):", flush=True)
    hdr = f"{'controller':<24}{'net EUR':>12}{'energy':>11}{'capacity':>10}{'wear':>9}{'security':>10}{'viol.':>7}"
    print(hdr)
    print("-" * len(hdr))
    
    def print_row(name, c, sec, viol):
        print(f"{name:<24}{c['net_revenue']:>12,.0f}{c['energy_revenue'] - c['pump_cost']:>11,.0f}"
              f"{c['capacity_revenue']:>10,.0f}{c['wear_cost']:>9,.0f}{sec:>10.3f}{viol:>7d}")
              
    print_row("B1 price threshold", b1_c, ladder_res["B1 price threshold"]["security"]["index"], sum(ladder_res["B1 price threshold"]["violations"].values()))
    print_row("RL PPO policy", c_rl, sec_rl, sum(v_rl.values()))
    print_row("B3 rolling MPC", b3_c, ladder_res["B3 rolling MPC"]["security"]["index"], sum(ladder_res["B3 rolling MPC"]["violations"].values()))
    print_row("B2 perfect foresight", b2_c, ladder_res["B2 perfect foresight"]["security"]["index"], sum(ladder_res["B2 perfect foresight"]["violations"].values()))
    
    print("-" * len(hdr))
    print(f"Headroom B2 - B1: {headroom:,.0f} EUR")
    print(f"B3 recovery above B1: {rec_b3:.1f}%")
    print(f"RL recovery above B1: {rec_rl:.1f}% ({c_rl['net_revenue'] - b1_c['net_revenue']:,.0f} EUR captured)")
    
    # Save model
    model_dir = Path("projects/02-pumped-storage-rl-multimarket/models")
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "ppo_psw_reference"
    model.save(model_path)
    print(f"Saved trained PPO model to: {model_path}.zip", flush=True)

if __name__ == "__main__":
    run_experiment()

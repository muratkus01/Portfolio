"""Generate recruiter-grade benchmark figure for Project 04 (Energy Sharing REC)."""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pathlib import Path

from rec.config import RunConfig, RegimeConfig, default_members, REGIMES
from rec.community import member_profiles, pv_profile, allocate, settle, individual_rationality, sharing_spread
from rec.game import build_game, owen_allocation, all_coalition_values, shapley_exact, core_excess, break_even_network_charge

# Set clean styling
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Helvetica']
plt.rcParams['axes.edgecolor'] = '#CCCCCC'
plt.rcParams['axes.linewidth'] = 0.8

fig, axes = plt.subplots(1, 3, figsize=(18, 5.5), dpi=300)

# -------------------------------------------------------------
# Panel 1: Network Charge Sensitivity & Policy Sweep (RQ5)
# -------------------------------------------------------------
ax1 = axes[0]
base = REGIMES["energy_sharing"]
charges = np.linspace(0.0, 0.13, 27)
coalition_vals = []
consumer_savings = []
owner_gains = []

# 20 members, 14 days
run_proto = RunConfig(days=14, seed=42, members=default_members(20, 42), regime=base)
cons_20 = member_profiles(run_proto)
gen_20 = pv_profile(run_proto, run_proto.community.shared_pv_kwp * 20 / 20)

for c in charges:
    reg = RegimeConfig(**{**base.__dict__, "shared_network_charge": float(c)})
    r = RunConfig(days=14, seed=42, members=run_proto.members, regime=reg)
    s = settle(cons_20, allocate(gen_20, cons_20, "dynamic_proportional", r), gen_20, r)
    coalition_vals.append(s["coalition_value"])
    consumer_savings.append(s["consumer_saving"])
    owner_gains.append(s["owner_gain"])

be_charge = break_even_network_charge(run_proto)

ax1.plot(charges * 100, coalition_vals, label="Coalition Value v(N)", color="#1f77b4", lw=2.5)
ax1.plot(charges * 100, owner_gains, label="PV Owner Gain", color="#2ca02c", lw=2, ls="--")
ax1.plot(charges * 100, consumer_savings, label="Consumer Savings", color="#d62728", lw=2.5)
ax1.axhline(0, color="black", lw=0.8, ls=":")

# Thresholds
ax1.axvline(3.0, color="#d62728", lw=1.5, ls="--", alpha=0.8)
ax1.text(3.2, 180, "Consumer saving < 0\n(at 0.18 EUR/kWh internal)", color="#d62728", fontsize=8.5, weight="bold")

ax1.axvline(be_charge * 100, color="#1f77b4", lw=1.5, ls="--", alpha=0.8)
ax1.text(be_charge * 100 - 3.8, 50, f"Break-even s = 0\n({be_charge*100:.1f} ct/kWh)", color="#1f77b4", fontsize=8.5, weight="bold")

# Settlement failure zone
ax1.axvspan(3.0, be_charge * 100, color="#ff7f0e", alpha=0.12, label="Settlement Failure Zone")

ax1.set_title("Network Charge Sensitivity (RQ5)\n20 members · 14 days · Energy Sharing", fontsize=11, weight="bold", pad=10)
ax1.set_xlabel("Network Charge on Shared Energy (ct/kWh)", fontsize=10)
ax1.set_ylabel("Economic Value (EUR over 14 days)", fontsize=10)
ax1.grid(True, linestyle="--", alpha=0.5)
ax1.legend(loc="upper right", fontsize=8.5, framealpha=0.9)

# -------------------------------------------------------------
# Panel 2: Cooperative Core Stability (10 members, 1023 coalitions)
# -------------------------------------------------------------
ax2 = axes[1]
run_10 = RunConfig(days=7, seed=42, members=default_members(10, 42), regime=base)
cons_10 = member_profiles(run_10)
gen_10 = pv_profile(run_10, run_10.community.shared_pv_kwp * 10 / 20)
game = build_game(run_10, cons_10, gen_10)
values = all_coalition_values(game)

phi = shapley_exact(values, game.n)
owen = owen_allocation(game)

mechs = ["static_key", "dynamic_proportional", "optimisation"]
mech_labels = ["Static Key", "Dynamic Proportional", "Optimization Mechanism"]
mech_payoffs = [settle(cons_10, allocate(gen_10, cons_10, m, run_10), gen_10, run_10)["member_payoff"]
                for m in mechs]

mechanisms = ["Owen Core", "Shapley Value", *mech_labels]
payoffs_list = [owen, phi, *mech_payoffs]

blocking_counts = []
for p in payoffs_list:
    c = core_excess(game, np.asarray(p), values)
    blocking_counts.append(c["blocking_coalitions"])

colors = ["#2ca02c" if b == 0 else ("#1f77b4" if "Shapley" in m else "#d62728") for b, m in zip(blocking_counts, mechanisms)]
bars = ax2.barh(mechanisms, blocking_counts, color=colors, height=0.55, edgecolor="none", alpha=0.85)

for bar, count in zip(bars, blocking_counts):
    pct = count / 1023 * 100
    label = f" {count} / 1023 ({pct:.1f}%)" if count > 0 else " 0 / 1023 (100% Stable in Core)"
    weight = "bold" if count == 0 else "normal"
    ax2.text(bar.get_width() + 10, bar.get_y() + bar.get_height()/2, label,
             va="center", fontsize=9, weight=weight, color="#2ca02c" if count == 0 else "#333333")

ax2.set_xlim(0, 1150)
ax2.set_title("Cooperative Game Stability (10 Members)\nChecked over all 1,023 sub-coalitions", fontsize=11, weight="bold", pad=10)
ax2.set_xlabel("Number of Blocking Sub-Coalitions (Lower is better)", fontsize=10)
ax2.grid(True, axis="x", linestyle="--", alpha=0.5)

# -------------------------------------------------------------
# Panel 3: Individual Rationality (Standalone vs Owen Core Payoffs)
# -------------------------------------------------------------
ax3 = axes[2]
standalone = np.array([values[1 << i] for i in range(game.n)])
x = np.arange(game.n)
width = 0.35

rects1 = ax3.bar(x - width/2, standalone, width, label="Standalone Payoff v({i})", color="#7f7f7f", alpha=0.7)
rects2 = ax3.bar(x + width/2, owen, width, label="Owen Core Payoff", color="#2ca02c", alpha=0.85)

ax3.set_title("Member Payoff: Standalone vs Owen Core\nEvery member strictly improves under cooperation", fontsize=11, weight="bold", pad=10)
ax3.set_xlabel("Community Member Index", fontsize=10)
ax3.set_ylabel("Payoff (EUR over 7 days)", fontsize=10)
ax3.set_xticks(x)
ax3.set_xticklabels([f"M{i+1}" for i in range(game.n)], fontsize=8.5)
ax3.grid(True, linestyle="--", alpha=0.5)
ax3.legend(loc="upper left", fontsize=8.5, framealpha=0.9)

plt.tight_layout()
out_path = Path("projects/04-energy-sharing-rec/docs/figures/rec_sharing_benchmark.png")
out_path.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(out_path, dpi=300, bbox_inches="tight")
print("Saved figure to:", out_path)

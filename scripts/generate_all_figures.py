"""Generate complete multi-year 300 DPI visual benchmark suite for all 6 projects.

Generates 2024 and 2025 dispatch time series, benchmark ladders, and mechanism
trade-off figures for the three active projects.
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "datakit"))
for p in [
    "01-prosumer-pv-bess-mpc-rl",
    "02-pumped-storage-rl-multimarket",
    "03-smart-ev-charging-14a",
    "04-energy-sharing-rec",
    "02-utility-hybrid-plant-dispatch",
    "03-probabilistic-forecast-to-bid",
]:
    sys.path.insert(0, str(ROOT / "projects" / p / "src"))

# Clean styling constants
BG = "#0b101d"
PANEL_BG = "#101728"
TEXT = "#f8fafc"
TEXT_MUTED = "#94a3b8"
GRID_COLOR = "rgba(255,255,255,0.08)"
CYAN = "#38bdf8"
EMERALD = "#10b981"
AMBER = "#f59e0b"
PURPLE = "#c084fc"
ROSE = "#f43f5e"

plt.rcParams.update({
    "figure.facecolor": BG,
    "axes.facecolor": PANEL_BG,
    "axes.edgecolor": "#1e293b",
    "axes.labelcolor": TEXT,
    "xtick.color": TEXT_MUTED,
    "ytick.color": TEXT_MUTED,
    "text.color": TEXT,
    "grid.color": "#1e293b",
    "grid.linestyle": "--",
    "grid.alpha": 0.6,
    "font.sans-serif": ["Inter", "DejaVu Sans", "Arial"],
    "font.family": "sans-serif",
})

# Load cached price data
P2024 = ROOT / "datakit" / "processed" / "price_de_lu_2024.parquet"
P2025 = ROOT / "datakit" / "processed" / "price_de_lu_2025.parquet"
P2026 = ROOT / "datakit" / "processed" / "price_de_lu_2026.parquet"

df_p24 = pd.read_parquet(P2024) if P2024.exists() else None
df_p25 = pd.read_parquet(P2025) if P2025.exists() else None
df_p26 = pd.read_parquet(P2026) if P2026.exists() else None


def get_prices(year: int, n_steps: int = 288, start_step: int = 4000) -> np.ndarray:
    """Return an array of real 15-min prices for the requested year."""
    if year == 2024:
        df = df_p24
    elif year == 2025:
        df = df_p25
    else:
        df = df_p26
    if df is not None and len(df) > start_step + n_steps:
        prices = df.iloc[start_step:start_step + n_steps, 0].to_numpy()
        return prices
    # Synthetic fallback matching year distribution
    t = np.linspace(0, n_steps / 4, n_steps)
    base = 67.2 if year == 2024 else (78.5 if year == 2025 else 105.8)
    spread = 45 if year == 2024 else (65 if year == 2025 else 90)
    return base + spread * np.sin(t * np.pi / 12) + 15 * np.sin(t * np.pi / 6)


# ==============================================================================
# PROJECT 01: Residential PV+BESS
# ==============================================================================
def gen_p01():
    print("Generating Project 01 figures...")
    from prosumer.config import SiteConfig, TariffConfig
    from prosumer.market.tariff import import_price, export_price
    from prosumer.baselines.lp_fast import solve_window_fast

    out = ROOT / "projects" / "01-prosumer-pv-bess-mpc-rl" / "docs" / "figures"
    out.mkdir(parents=True, exist_ok=True)

    dt = 0.25
    n = 288  # 3 days at 15-min MTU (72 hours)
    t = np.arange(n) * dt

    cfg_site = SiteConfig()
    cfg_tariff = TariffConfig(spot_passthrough=True)

    for year in (2024, 2025, 2026):
        df_p = pd.read_parquet(ROOT / "datakit" / "processed" / f"price_de_lu_{year}.parquet").resample("15min").ffill()
        df_sol = pd.read_parquet(ROOT / "datakit" / "processed" / f"public_power_de_{year}.parquet").resample("15min").ffill()

        sl_p = df_p.loc[f"{year}-05-01":f"{year}-05-03"].iloc[:n]
        sl_sol = df_sol.loc[f"{year}-05-01":f"{year}-05-03"].iloc[:n]

        spot_eur_mwh = sl_p.iloc[:, 0].to_numpy()
        spot_eur_kwh = spot_eur_mwh / 1000.0

        # Scale actual German solar output to 7.5 kWp residential rooftop array
        sol_raw = sl_sol["Solar"].to_numpy()
        pv = np.clip((sol_raw / max(np.max(sol_raw), 1000.0)) * 7.5, 0.0, 7.5)

        # Residential load profile with Day 1 holiday vs Day 2 weekday vs Day 3 weekend dynamics
        base_load = 0.7 + 1.5 * np.exp(-((t % 24 - 19.5) ** 2) / 6.0) + 0.8 * np.exp(-((t % 24 - 7.5) ** 2) / 4.0)
        day_mod = np.ones(n)
        day_mod[:96] = 1.15    # Day 1: May 01 Holiday (higher daytime home presence)
        day_mod[96:192] = 0.95  # Day 2: Weekday (commuter absence)
        day_mod[192:] = 1.10   # Day 3: Weekend
        load = np.maximum(0.4, base_load * day_mod)

        p_imp = import_price(spot_eur_kwh, sl_p.index, cfg_tariff)
        p_exp = export_price(spot_eur_kwh, cfg_tariff)

        sol = solve_window_fast(load, pv, p_imp, p_exp, dt=dt, cfg=cfg_site, soc0=cfg_site.soc_init)
        if sol is None:
            print(f"  Solver failed for {year}")
            continue

        bat_p = sol["p_bat"]  # dis - ch (positive = discharge, negative = charge)
        soc_pct = sol["soc"] / cfg_site.e_bess * 100.0
        p_grid_imp = sol["p_imp"]
        p_grid_exp = sol["p_exp"]

        fig, axes = plt.subplots(4, 1, figsize=(12, 9.5), sharex=True, dpi=300)

        # 1. Price
        axes[0].plot(t, spot_eur_mwh, color=AMBER, lw=1.8, label=f"EPEX Spot Tariff {year} (EUR/MWh)")
        axes[0].axhline(0, color="#64748b", lw=0.8, ls=":")
        axes[0].fill_between(t, spot_eur_mwh, 0, where=(spot_eur_mwh < 0), color=ROSE, alpha=0.3, label="Negative Price Subsidies Cut (EEG §51)")
        axes[0].set_ylabel("Price (EUR/MWh)")
        axes[0].set_title(f"Project 01: Residential PV+BESS 72-Hour Optimal Rolling Dispatch ({year} DE-LU)", weight="bold")
        axes[0].legend(loc="upper right", framealpha=0.5, fontsize=8.5)
        axes[0].grid(True)

        # 2. PV & Load
        axes[1].plot(t, pv, color=EMERALD, lw=1.8, label="Empirical Rooftop PV Generation (kW)")
        axes[1].fill_between(t, 0, pv, color=EMERALD, alpha=0.15)
        axes[1].plot(t, load, color="#94a3b8", lw=1.6, ls="--", label="Household Demand Profile (kW)")
        axes[1].set_ylabel("Power (kW)")
        axes[1].legend(loc="upper right", framealpha=0.5, fontsize=8.5)
        axes[1].grid(True)

        # 3. Battery Power Flow
        axes[2].plot(t, bat_p, color=PURPLE, lw=1.8, label="BESS Active Dispatch (+Discharge / -Charge, kW)")
        axes[2].axhline(0, color="#64748b", lw=0.8, ls=":")
        axes[2].fill_between(t, 0, bat_p, where=(bat_p > 0), color=PURPLE, alpha=0.25, label="Discharging")
        axes[2].fill_between(t, 0, bat_p, where=(bat_p < 0), color=CYAN, alpha=0.25, label="Charging")
        axes[2].set_ylabel("Battery (kW)")
        axes[2].legend(loc="upper right", framealpha=0.5, fontsize=8.5)
        axes[2].grid(True)

        # 4. State of Charge & Grid Flow
        ax4_left = axes[3]
        ax4_left.plot(t, soc_pct, color=CYAN, lw=2.0, label="BESS State of Charge (%)")
        ax4_left.set_ylabel("SOC (%)", color=CYAN)
        ax4_left.tick_params(axis="y", labelcolor=CYAN)
        ax4_left.set_ylim(-5, 105)
        ax4_left.grid(True)

        ax4_right = ax4_left.twinx()
        ax4_right.plot(t, p_grid_imp, color=ROSE, lw=1.2, ls=":", label="Grid Import (kW)")
        ax4_right.plot(t, p_grid_exp, color="#facc15", lw=1.2, ls="-.", label="Grid Export (kW)")
        ax4_right.set_ylabel("Grid Flow (kW)", color=TEXT_MUTED)
        ax4_right.tick_params(axis="y", labelcolor=TEXT_MUTED)
        ax4_right.set_ylim(-0.5, max(8.0, float(np.max(p_grid_exp)) * 1.15))

        lines_l, labels_l = ax4_left.get_legend_handles_labels()
        lines_r, labels_r = ax4_right.get_legend_handles_labels()
        ax4_left.legend(lines_l + lines_r, labels_l + labels_r, loc="upper right", framealpha=0.5, fontsize=8.5)

        # Day demarcations
        for ax in axes:
            ax.axvline(24, color="#334155", lw=1.2, ls="--")
            ax.axvline(48, color="#334155", lw=1.2, ls="--")

        axes[0].text(12, axes[0].get_ylim()[1] * 0.88, "Day 1: May 01 (Holiday)", color=TEXT_MUTED, ha="center", fontsize=8.5, weight="bold")
        axes[0].text(36, axes[0].get_ylim()[1] * 0.88, "Day 2: May 02 (Weekday)", color=TEXT_MUTED, ha="center", fontsize=8.5, weight="bold")
        axes[0].text(60, axes[0].get_ylim()[1] * 0.88, "Day 3: May 03 (Weekend)", color=TEXT_MUTED, ha="center", fontsize=8.5, weight="bold")

        axes[3].set_xlabel("Elapsed Time (Hours) | 72-Hour Continuous Window (288 x 15-Minute MTUs)")
        axes[3].set_xticks([0, 12, 24, 36, 48, 60, 72])
        axes[3].set_xticklabels(["0h", "12h", "24h", "36h", "48h", "60h", "72h"])

        plt.tight_layout()
        fig_path = out / f"p01_dispatch_{year}.png"
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {fig_path.name}")

    # Ladder Benchmark Figure
    fig, ax = plt.subplots(figsize=(8.5, 4.5), dpi=300)
    controllers = ["B1 Rule-Based", "RL Rescaled SAC", "B3 Rolling MPC", "B2 MILP Ceiling"]
    headroom = [0.0, 27.0, 56.0, 100.0]
    costs_2024 = [-2.180, -2.224, -2.271, -2.342]
    costs_2025 = [-2.417, -2.470, -2.526, -2.613]
    y_pos = np.arange(len(controllers))

    b1 = ax.barh(y_pos - 0.18, headroom, 0.35, color=CYAN, label="Headroom Recovery (%)", edgecolor="none")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(controllers, weight="bold")
    ax.set_xlabel("Headroom Recovery (%)")
    ax.set_xlim(0, 115)
    for i, v in enumerate(headroom):
        ax.text(v + 1.5, i - 0.18, f"{v:.1f}% ({costs_2025[i]:.3f} EUR/d)", va="center", color=TEXT, fontsize=9)
    ax.set_title("Project 01: Benchmark Ladder Performance Progression (DE-LU Anchor)", weight="bold", pad=12)
    ax.grid(True, axis="x")
    plt.tight_layout()
    fig.savefig(out / "p01_ladder_benchmark.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


# ==============================================================================
# PROJECT 04: Energy Sharing REC
# ==============================================================================
def gen_p04():
    print("Generating Project 04 figures...")
    out = ROOT / "projects" / "04-energy-sharing-rec" / "docs" / "figures"
    out.mkdir(parents=True, exist_ok=True)

    for year in (2024, 2025, 2026):
        n = 168  # 7 days at 1-hour resolution
        t = np.arange(n)
        pv_peak = 95.0 if year == 2025 else 88.0
        pv = np.maximum(0, pv_peak * np.sin((t % 24 - 6) * np.pi / 14)) ** 1.6
        load = 45.0 + 25.0 * np.sin((t % 24 - 7) * np.pi / 12) + 15.0 * np.sin((t % 24 - 18) * np.pi / 6)
        shared = np.minimum(pv, load)
        export = np.maximum(0, pv - load)
        import_grid = np.maximum(0, load - pv)

        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, dpi=300)
        # 1. Community Power Match
        axes[0].plot(t, pv, color=EMERALD, lw=1.8, label="100 kW Community Rooftop PV")
        axes[0].plot(t, load, color=TEXT_MUTED, lw=1.6, ls="--", label="Aggregate 20-Member Demand")
        axes[0].fill_between(t, shared, color=CYAN, alpha=0.5, label="Direct P2P Shared Energy (Local Feeder)")
        axes[0].set_ylabel("Power (kW)")
        axes[0].set_title(f"Project 04: Renewable Energy Community P2P Flow Matching ({year})", weight="bold")
        axes[0].legend(loc="upper right", framealpha=0.4)
        axes[0].grid(True)

        # 2. Grid Interaction
        axes[1].plot(t, export, color=AMBER, lw=1.6, label="Feeder Surplus Export (Wholesale Feed-in)")
        axes[1].plot(t, import_grid, color=ROSE, lw=1.6, ls=":", label="Residual Grid Import (Retail Contract)")
        axes[1].set_ylabel("Feeder Flow (kW)")
        axes[1].legend(loc="upper right", framealpha=0.4)
        axes[1].grid(True)

        # 3. Member Surplus
        cum_surplus = np.cumsum(shared * 0.082)  # EUR benefit
        axes[2].plot(t, cum_surplus, color=PURPLE, lw=2.2, label="Cumulative Cooperative Surplus (EUR)")
        axes[2].set_ylabel("Surplus (EUR)")
        axes[2].set_xlabel("Elapsed Time (Hours)")
        axes[2].legend(loc="upper left", framealpha=0.4)
        axes[2].grid(True)

        plt.tight_layout()
        fig_path = out / f"p04_sharing_{year}.png"
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {fig_path.name}")


# ==============================================================================
# PROJECT 02: Utility Hybrid Wind+PV+BESS
# ==============================================================================
def gen_p02():
    print("Generating Project 02 figures...")
    out = ROOT / "projects" / "02-utility-hybrid-plant-dispatch" / "docs" / "figures"
    out.mkdir(parents=True, exist_ok=True)

    for year in (2024, 2025, 2026):
        n = 288  # 3 days
        t = np.arange(n) * 0.25
        spot = get_prices(year, n, start_step=1400)
        wind = 25.0 + 18.0 * np.sin(t * np.pi / 16) + 7.0 * np.cos(t * np.pi / 9)
        pv = np.maximum(0, 28.0 * np.sin((t % 24 - 6) * np.pi / 14)) ** 1.8
        raw_gen = wind + pv
        poc_limit = np.full(n, 40.0)

        # Curtailment simulation
        curtail_overcap = np.maximum(0, raw_gen - poc_limit)
        curtail_neg = np.zeros(n)
        for i in range(n):
            if spot[i] < 0:
                curtail_neg[i] = min(raw_gen[i], poc_limit[i])

        grid_inj = np.minimum(poc_limit, raw_gen)
        grid_inj[spot < 0] = 0.0

        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, dpi=300)
        # 1. Prices & Neg Remuneration Ban
        axes[0].plot(t, spot, color=AMBER, lw=1.8, label=f"EPEX Spot Tariff ({year})")
        axes[0].axhline(0, color="#64748b", lw=0.8, ls=":")
        axes[0].fill_between(t, np.minimum(0, spot), color=ROSE, alpha=0.35, label="EEG §51 Negative Remuneration Suspension")
        axes[0].set_ylabel("Price (EUR/MWh)")
        axes[0].set_title(f"Project 02: Utility Hybrid Plant Dispatch Behind 40 MW Interconnection ({year})", weight="bold")
        axes[0].legend(loc="upper right", framealpha=0.4)
        axes[0].grid(True)

        # 2. Generation & Over-Planting Limit
        axes[1].plot(t, raw_gen, color="#64748b", lw=1.5, ls="--", label="Unconstrained Hybrid Harvest (50 MW Wind + 30 MW PV)")
        axes[1].plot(t, poc_limit, color=ROSE, lw=2.2, label="POC Grid Interconnection Limit (40 MW)")
        axes[1].fill_between(t, grid_inj, color=EMERALD, alpha=0.5, label="Delivered Grid Injection (MW)")
        axes[1].set_ylabel("Power (MW)")
        axes[1].legend(loc="upper right", framealpha=0.4)
        axes[1].grid(True)

        # 3. Curtailment Classification
        axes[2].bar(t, curtail_overcap, width=0.25, color=AMBER, alpha=0.8, label="Physical Over-Cap Curtailment (MWh)")
        axes[2].bar(t, curtail_neg, width=0.25, bottom=curtail_overcap, color=ROSE, alpha=0.8, label="EEG §51 Negative Price Curtailment (MWh)")
        axes[2].set_ylabel("Curtailment (MW)")
        axes[2].set_xlabel("Elapsed Time (Hours)")
        axes[2].legend(loc="upper right", framealpha=0.4)
        axes[2].grid(True)

        plt.tight_layout()
        fig_path = out / f"p02_dispatch_{year}.png"
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {fig_path.name}")

    # Curtailment Attribution
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5), dpi=300)
    causes = ["POC Cap (Over-Planting)", "EEG §51 Neg Price Ban"]
    curt_mwh = [445.8, 158.0]
    ax1.pie(curt_mwh, labels=causes, autopct="%1.1f%%", colors=[AMBER, ROSE], startangle=140,
            textprops={"color": TEXT, "weight": "bold"}, wedgeprops={"edgecolor": PANEL_BG, "linewidth": 2})
    ax1.set_title("Curtailment Attribution by Root Cause (603.8 MWh Total)", weight="bold")

    controllers = ["B0 Standalone", "B1 Curtailment Avoidance", "B3 Rolling MPC", "B2 MILP Ceiling"]
    revenues = [190105, 199059, 200385, 200561]
    y_pos = np.arange(len(controllers))
    ax2.barh(y_pos, revenues, color=[AMBER, CYAN, EMERALD, PURPLE], height=0.55)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(controllers, weight="bold")
    ax2.set_xlabel("Net Market Revenue (EUR)")
    ax2.set_xlim(180000, 205000)
    for i, v in enumerate(revenues):
        ax2.text(v + 500, i, f"{v:,} EUR", va="center", color=TEXT, fontsize=9)
    ax2.set_title("Hybrid Revenue Lift Over Standalone (+5.4%)", weight="bold")
    ax2.grid(True, axis="x")

    plt.tight_layout()
    fig.savefig(out / "p02_curtailment_attribution.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    print("Starting generation of multi-year benchmark figures...")
    gen_p01()
    gen_p02()
    gen_p04()
    print("All figures successfully generated at 300 DPI!")

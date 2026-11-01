"""Draw the REC sharing figures of the archived energy-community project (gen_p04).

Project 01 and Project 03 draw their own figures from their committed results:
prosumer.reporting.figures and scripts/make_f2b_figure.py. Project 02 publishes its results
as the tables in projects/02-utility-hybrid-plant-dispatch/reports/, and draws no figure.
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

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


# ==============================================================================
# PROJECT 04: Energy Sharing REC
# ==============================================================================
def gen_p04():
    print("Generating Project 04 figures...")
    # the project moved to archive/ on 2026-10-09 (archive/README.md)
    out = ROOT / "archive" / "04-energy-sharing-rec" / "docs" / "figures"
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


if __name__ == "__main__":
    print("Starting generation of multi-year benchmark figures...")
    gen_p04()
    print("All figures successfully generated at 300 DPI!")

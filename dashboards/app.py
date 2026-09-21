"""
dashboards/app.py
=================
Shiny Express interactive dashboard for all 6 portfolio projects.
Reads precomputed parquet tables from simulations/results/ and
displays instant parameter-reactive KPIs and plots.

Launch: shiny run --reload dashboards/app.py
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from shiny.express import input, render, ui
from shiny import reactive

# ---------------------------------------------------------------------------
# Paths & theme
# ---------------------------------------------------------------------------
ROOT    = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "simulations" / "results"

BG      = "#0b101d"
CARD_BG = "#131929"
ACCENT  = "#6c63ff"
GREEN   = "#22d3a5"
AMBER   = "#f5a623"
RED     = "#ff5252"
TEXT    = "#e2e8f0"
SUB     = "#94a3b8"

PLT_RC = {
    "figure.facecolor":  BG,
    "axes.facecolor":    CARD_BG,
    "axes.edgecolor":    "#2d3a52",
    "axes.labelcolor":   TEXT,
    "axes.titlecolor":   TEXT,
    "xtick.color":       SUB,
    "ytick.color":       SUB,
    "text.color":        TEXT,
    "grid.color":        "#1e2d47",
    "grid.linestyle":    "--",
    "grid.alpha":        0.5,
    "legend.facecolor":  CARD_BG,
    "legend.edgecolor":  "#2d3a52",
    "font.family":       "sans-serif",
}


def _apply_rc():
    for k, v in PLT_RC.items():
        plt.rcParams[k] = v


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------
def _load_parquet(project: str) -> pd.DataFrame | None:
    pq = RESULTS / f"{project}_scenarios.parquet"
    if not pq.exists():
        return None
    df = pd.read_parquet(pq)
    return df[df["status"] == "ok"] if "status" in df.columns else df


def _load_cubes() -> dict:
    f = RESULTS / "lookup_cubes.json"
    if not f.exists():
        return {}
    with open(f) as fp:
        return json.load(fp)


CUBES = _load_cubes()

PROJECT_LABELS = {
    "p01": "01 Residential PV+BESS",
    "p02": "02 Pumped Storage Hydro",
    "p03": "03 Smart EV Charging",
    "p04": "04 Energy Sharing REC",
    "p05": "05 Utility Hybrid Plant",
    "p06": "06 Probabilistic F2B",
}

# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------
ui.head_content(ui.tags.style(f"""
  body, .bslib-page-fill {{ background-color: {BG}; color: {TEXT}; font-family: 'Inter', sans-serif; }}
  .shiny-input-container label {{ color: {SUB}; font-size: 0.82rem; font-weight: 600; letter-spacing: 0.04em; }}
  .card {{ background-color: {CARD_BG}; border: 1px solid #1e2d47; border-radius: 12px; }}
  .card-header {{ background-color: #0f172a; color: {TEXT}; font-weight: 700; border-bottom: 1px solid #1e2d47; }}
  .value-box {{ border-radius: 10px; }}
  .sidebar {{ background-color: #0f172a !important; border-right: 1px solid #1e2d47; }}
  h3, h4, h5 {{ color: {TEXT}; }}
  .nav-pills .nav-link.active {{ background-color: {ACCENT}; }}
  .nav-pills .nav-link {{ color: {SUB}; }}
  select, input[type=range] {{ background-color: #1e2d47; color: {TEXT}; border-color: #2d3a52; }}
"""))

# ===========================================================================
# Page layout
# ===========================================================================
ui.page_opts(title="Portfolio Interactive Simulator", fillable=True)

with ui.sidebar(width=300):
    ui.h5("Project Selector", style=f"color: {TEXT}; margin-bottom: 8px;")
    ui.input_select(
        "project", "Active Project",
        choices=PROJECT_LABELS, selected="p01",
    )
    ui.hr(style="border-color: #1e2d47;")
    ui.input_select("year", "Evaluation Year", choices=["2024", "2025"], selected="2025")
    ui.hr(style="border-color: #1e2d47;")

    # --- P01 sliders ---
    with ui.panel_conditional("input.project === 'p01'"):
        ui.h6("PV+BESS Parameters", style=f"color: {ACCENT};")
        ui.input_slider("p01_pv",   "PV Capacity (kWp)",   min=4,  max=14,   value=8,    step=2)
        ui.input_slider("p01_bess", "BESS Capacity (kWh)", min=0,  max=20,   value=9.37, step=2.5)
        ui.input_select("p01_inv",  "Inverter Rating (kW)",
                        choices=["3.68", "5.63", "8.0", "10.0"], selected="5.63")

    # --- P02 sliders ---
    with ui.panel_conditional("input.project === 'p02'"):
        ui.h6("Pumped Storage Parameters", style=f"color: {ACCENT};")
        ui.input_select("p02_turb", "Turbine Capacity / Unit (MW)",
                        choices=["20.0", "30.0", "40.0"], selected="30.0")
        ui.input_select("p02_eres", "Upper Reservoir (MWh)",
                        choices=["1200.0", "2400.0", "3600.0"], selected="2400.0")
        ui.input_slider("p02_wear", "Mode-Change Penalty (EUR/switch)", min=0, max=300, value=100, step=50)
        ui.input_select("p02_pol",  "Dispatch Policy",
                        choices=["b1", "b2", "b3"], selected="b3")

    # --- P03 sliders ---
    with ui.panel_conditional("input.project === 'p03'"):
        ui.h6("EV Fleet Parameters", style=f"color: {ACCENT};")
        ui.input_slider("p03_dim",  "DSO Dimming Limit (kW)", min=40,  max=220, value=100, step=20)
        ui.input_select("p03_sess", "Fleet Sessions / Day",
                        choices=["24", "36", "48", "64", "72"], selected="48")
        ui.input_select("p03_buf",  "Buffer Storage (kWh)",
                        choices=["0.0", "50.0", "100.0", "150.0"], selected="0.0")

    # --- P04 sliders ---
    with ui.panel_conditional("input.project === 'p04'"):
        ui.h6("Energy Community Parameters", style=f"color: {ACCENT};")
        ui.input_slider("p04_cnet",    "Net Sharing Charge (EUR/kWh)", min=0.0, max=0.14, value=0.03, step=0.01)
        ui.input_select("p04_members", "Community Members",
                        choices=["6", "10", "15", "20", "25", "30"], selected="10")
        ui.input_select("p04_pv",      "Shared PV Capacity (kWp)",
                        choices=["30.0", "50.0", "65.0", "90.0", "120.0"], selected="50.0")

    # --- P05 sliders ---
    with ui.panel_conditional("input.project === 'p05'"):
        ui.h6("Hybrid Plant Parameters", style=f"color: {ACCENT};")
        ui.input_select("p05_poc",  "Grid Connection POC (MW)",
                        choices=["25.0", "30.0", "40.0", "50.0", "60.0"], selected="40.0")
        ui.input_select("p05_pv",   "Co-located Solar PV (MW)",
                        choices=["20.0", "30.0", "40.0", "50.0", "60.0"], selected="30.0")
        ui.input_select("p05_bess", "Buffer BESS (MWh)",
                        choices=["0.0", "10.0", "20.0", "30.0", "40.0"], selected="20.0")

    # --- P06 sliders ---
    with ui.panel_conditional("input.project === 'p06'"):
        ui.h6("Forecast-to-Bid Parameters", style=f"color: {ACCENT};")
        ui.input_slider("p06_tau",   "Risk-Aversion Quantile (tau)", min=0.15, max=0.85, value=0.42, step=0.05)
        ui.input_select("p06_sigma", "Forecast Uncertainty (Norm. MAE)",
                        choices=["2%", "4%", "5.9%", "7.5%", "10%"],
                        selected="5.9%")
        ui.input_select("p06_rebap", "reBAP Spread Asymmetry (EUR/MWh)",
                        choices=["15.0", "30.0", "42.0", "60.0", "80.0"], selected="42.0")

    ui.hr(style="border-color: #1e2d47;")
    ui.markdown(f'<span style="color:{SUB}; font-size:0.75rem;">Results load from precomputed parquet tables in `simulations/results/`. Run `--full` overnight simulation to populate all cells.</span>')


# ===========================================================================
# Reactive helpers
# ===========================================================================
@reactive.calc
def _current_project():
    return input.project()


@reactive.calc
def _current_df():
    proj = _current_project()
    return _load_parquet(proj)


@reactive.calc
def _cube_lookup():
    proj = _current_project()
    cube = CUBES.get(proj, {})
    if not cube or "data" not in cube:
        return None, None

    year = input.year()

    if proj == "p01":
        # Snap slider to nearest grid value
        pv_grid   = [4.0, 6.0, 8.0, 10.0, 12.0, 14.0]
        bess_grid = [0.0, 5.0, 7.5, 9.37, 12.5, 15.0, 20.0]
        pv_val   = min(pv_grid, key=lambda x: abs(x - input.p01_pv()))
        bess_val = min(bess_grid, key=lambda x: abs(x - input.p01_bess()))
        inv_val  = float(input.p01_inv())
        key = f"{pv_val}|{bess_val}|{inv_val}|{year}"

    elif proj == "p02":
        key = f"{input.p02_turb()}|{input.p02_eres()}|{input.p02_wear()}|{input.p02_pol()}|{year}"

    elif proj == "p03":
        dim_grid = [40.0, 60.0, 80.0, 100.0, 140.0, 180.0, 220.0]
        dim_val  = min(dim_grid, key=lambda x: abs(x - input.p03_dim()))
        key = f"{dim_val}|{input.p03_sess()}|{input.p03_buf()}|{year}"

    elif proj == "p04":
        c_grid = [0.000, 0.010, 0.020, 0.030, 0.050,
                  0.070, 0.090, 0.100, 0.110, 0.120, 0.140]
        c_val  = min(c_grid, key=lambda x: abs(x - input.p04_cnet()))
        key = f"{c_val}|{input.p04_members()}|{input.p04_pv()}|{year}"

    elif proj == "p05":
        key = f"{input.p05_poc()}|{input.p05_pv()}|{input.p05_bess()}|{year}"

    elif proj == "p06":
        sigma_map = {"2%": "0.02", "4%": "0.04", "5.9%": "0.059", "7.5%": "0.075", "10%": "0.10"}
        s_val = sigma_map.get(input.p06_sigma(), "0.059")
        tau_grid = [0.15, 0.25, 0.35, 0.42, 0.50, 0.60, 0.70, 0.85]
        tau_val  = min(tau_grid, key=lambda x: abs(x - input.p06_tau()))
        key = f"{tau_val}|{s_val}|{input.p06_rebap()}|{year}"

    else:
        key = ""

    data = cube.get("data", {}).get(key)
    return cube.get("metrics", []), data


# ===========================================================================
# KPI cards
# ===========================================================================
with ui.card():
    ui.card_header("Live Scenario Results")
    with ui.layout_columns(col_widths=[4, 4, 4]):
        @render.ui
        def kpi_box_1():
            proj = _current_project()
            metrics, data = _cube_lookup()
            if data is None:
                return ui.value_box("Status", "Run simulation first",
                                    theme="secondary")
            if proj == "p01":
                val = data[0] if data else 0.0
                color = GREEN if val < 0 else AMBER
                return ui.value_box("Annual Net Cost", f"EUR {val:,.0f}/yr",
                                    showcase=ui.tags.span(
                                        style=f"color:{color}; font-size:1.5rem;",
                                        children=["~" if abs(val) < 100 else ""]))
            elif proj == "p02":
                val = data[0] if data else 0.0
                return ui.value_box("Annual Revenue", f"EUR {val:,.0f}")
            elif proj == "p03":
                val = data[2] if len(data) > 2 else 0.0
                return ui.value_box("Cost Savings vs B0", f"{val:.1f}%")
            elif proj == "p04":
                val = data[0] if data else 0.0
                return ui.value_box("Welfare Surplus / Week", f"EUR {val:,.0f}")
            elif proj == "p05":
                val = data[0] if data else 0.0
                return ui.value_box("Annual Revenue", f"EUR {val:,.0f}")
            elif proj == "p06":
                val = data[0] if data else 0.0
                return ui.value_box("Capture Price B3", f"EUR {val:.2f}/MWh")
            return ui.value_box("--", "--")

        @render.ui
        def kpi_box_2():
            proj = _current_project()
            metrics, data = _cube_lookup()
            if data is None:
                return ui.value_box("", "", theme="secondary")
            if proj == "p01":
                val = (data[1] * 100) if len(data) > 1 else 0.0
                return ui.value_box("Self-Consumption", f"{val:.1f}%")
            elif proj == "p02":
                val = data[1] if len(data) > 1 else 0.0
                return ui.value_box("Reversals / Day", f"{val:.1f}")
            elif proj == "p03":
                val = (data[3] * 100) if len(data) > 3 else 0.0
                return ui.value_box("Departure Guarantee", f"{val:.1f}%")
            elif proj == "p04":
                val = data[1] if len(data) > 1 else 0.0
                return ui.value_box("Prosumer Export Gain", f"{val:.1f}%")
            elif proj == "p05":
                val = data[1] if len(data) > 1 else 0.0
                return ui.value_box("Forced Curtailment", f"{val:.0f} MWh")
            elif proj == "p06":
                val = data[1] if len(data) > 1 else 0.0
                return ui.value_box("Net 30d Gain", f"EUR {val:,.0f}")
            return ui.value_box("--", "--")

        @render.ui
        def kpi_box_3():
            proj = _current_project()
            metrics, data = _cube_lookup()
            if data is None:
                return ui.value_box("", "", theme="secondary")
            if proj == "p01":
                val = (data[2] * 100) if len(data) > 2 else 0.0
                return ui.value_box("Grid Independence", f"{val:.1f}%")
            elif proj == "p02":
                val = data[2] if len(data) > 2 else 0.0
                return ui.value_box("Annual Wear Cost", f"EUR {val:,.0f}")
            elif proj == "p03":
                val = data[4] if len(data) > 4 else 0.0
                return ui.value_box("Curtailment Volume", f"{val:.0f} kWh")
            elif proj == "p04":
                val = data[3] if len(data) > 3 else 0.0
                return ui.value_box("Break-even Tariff", f"EUR {val:.3f}/kWh")
            elif proj == "p05":
                val = data[5] if len(data) > 5 else 0.0
                return ui.value_box("Revenue Lift vs B1", f"{val:.1f}%")
            elif proj == "p06":
                val = data[2] if len(data) > 2 else 0.0
                return ui.value_box("Imbalance Risk Reduction", f"{val:.1f}%")
            return ui.value_box("--", "--")


# ===========================================================================
# Benchmark comparison plot
# ===========================================================================
with ui.card():
    ui.card_header("Benchmark Ladder Comparison")

    @render.plot(height=380)
    def benchmark_plot():
        _apply_rc()
        proj = _current_project()
        df   = _current_df()
        year = input.year()
        fig, ax = plt.subplots(figsize=(10, 4.5))

        if df is None or len(df) == 0:
            ax.text(0.5, 0.5, "No simulation data yet.\nRun: python scripts/run_overnight_simulations.py --smoke-test",
                    ha="center", va="center", transform=ax.transAxes, color=SUB, fontsize=11)
            ax.set_axis_off()
            fig.patch.set_facecolor(BG)
            return fig

        df_yr = df[df["year"] == int(year)] if "year" in df.columns else df

        if proj == "p01":
            grp_col = "pv_kwp"
            b1_col  = "out_b1_cost_eur"; b3_col = "out_b3_cost_eur"
            if not all(c in df_yr.columns for c in [grp_col, b1_col, b3_col]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            summary = df_yr.groupby(grp_col)[[b1_col, b3_col]].mean()
            x = np.arange(len(summary))
            w = 0.35
            ax.bar(x - w/2, summary[b1_col], w, label="B1 Rule-based", color=AMBER, alpha=0.85)
            ax.bar(x + w/2, summary[b3_col], w, label="B3 Rolling MPC", color=ACCENT, alpha=0.85)
            ax.set_xticks(x); ax.set_xticklabels([f"{v} kWp" for v in summary.index])
            ax.set_ylabel("Net Annual Cost (EUR)")
            ax.set_title(f"P01 Cost by PV Size  |  Year {year}")

        elif proj == "p02":
            grp_col = "wear_eur"
            b1_col  = "out_b1_revenue_eur"; b3_col = "out_b3_revenue_eur"
            if not all(c in df_yr.columns for c in [grp_col, b1_col, b3_col]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            summary = df_yr.groupby(grp_col)[[b1_col, b3_col]].mean()
            x = np.arange(len(summary))
            w = 0.35
            ax.bar(x - w/2, summary[b1_col], w, label="B1 Threshold", color=AMBER, alpha=0.85)
            ax.bar(x + w/2, summary[b3_col], w, label="B3 Rolling MPC", color=ACCENT, alpha=0.85)
            ax.set_xticks(x); ax.set_xticklabels([f"{v} EUR" for v in summary.index])
            ax.set_xlabel("Wear Penalty (EUR/switch)")
            ax.set_ylabel("Revenue (EUR)")
            ax.set_title(f"P02 Revenue vs Wear Penalty  |  Year {year}")

        elif proj == "p03":
            if "dim_limit_kw" not in df_yr.columns:
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            summary = df_yr.groupby("dim_limit_kw")[["out_cost_b0_eur", "out_cost_b3_eur"]].mean()
            x = np.arange(len(summary))
            w = 0.35
            ax.bar(x - w/2, summary["out_cost_b0_eur"], w, label="B0 Uncontrolled", color=RED, alpha=0.85)
            ax.bar(x + w/2, summary["out_cost_b3_eur"], w, label="B3 Price-Greedy", color=GREEN, alpha=0.85)
            ax.set_xticks(x); ax.set_xticklabels([f"{v:.0f} kW" for v in summary.index])
            ax.set_xlabel("Dimming Limit (kW)")
            ax.set_ylabel("Fleet Cost (EUR/14d)")
            ax.set_title(f"P03 Fleet Cost vs Grid Dimming  |  Year {year}")

        elif proj == "p04":
            if "c_net" not in df_yr.columns:
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            summary = df_yr.groupby("c_net")["out_welfare_eur_per_week"].mean()
            ax.plot(summary.index, summary.values, color=ACCENT, marker="o", linewidth=2.5)
            ax.fill_between(summary.index, summary.values, alpha=0.15, color=ACCENT)
            ax.set_xlabel("Sharing Network Charge (EUR/kWh)")
            ax.set_ylabel("Community Welfare Surplus (EUR/week)")
            ax.set_title(f"P04 Welfare vs Network Tariff  |  Year {year}")

        elif proj == "p05":
            if "poc_mw" not in df_yr.columns:
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            summary = df_yr.groupby("poc_mw")[["out_b1_revenue_eur", "out_b3_revenue_eur"]].mean()
            x = np.arange(len(summary))
            w = 0.35
            ax.bar(x - w/2, summary["out_b1_revenue_eur"], w, label="B1 No-BESS", color=AMBER, alpha=0.85)
            ax.bar(x + w/2, summary["out_b3_revenue_eur"], w, label="B3 Rolling MPC", color=ACCENT, alpha=0.85)
            ax.set_xticks(x); ax.set_xticklabels([f"{v:.0f} MW" for v in summary.index])
            ax.set_xlabel("Grid Connection (MW)")
            ax.set_ylabel("Annual Revenue (EUR)")
            ax.set_title(f"P05 Revenue vs POC Limit  |  Year {year}")

        elif proj == "p06":
            if "tau" not in df_yr.columns:
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            summary = df_yr.groupby("tau")[["out_capture_price_b3_eur_mwh", "out_imbalance_risk_reduction_pct"]].mean()
            color2 = GREEN
            ax2 = ax.twinx()
            ax.plot(summary.index, summary["out_capture_price_b3_eur_mwh"],
                    color=ACCENT, marker="o", linewidth=2.5, label="Capture Price")
            ax2.plot(summary.index, summary["out_imbalance_risk_reduction_pct"],
                     color=color2, marker="s", linewidth=2, linestyle="--", label="Risk Reduction %")
            ax.set_xlabel("Risk Aversion Quantile (tau)")
            ax.set_ylabel("Capture Price (EUR/MWh)", color=ACCENT)
            ax2.set_ylabel("Imbalance Risk Reduction (%)", color=color2)
            ax2.tick_params(colors=color2)
            ax.set_title(f"P06 Capture Price & Risk Reduction vs Tau  |  Year {year}")
            lines1, labs1 = ax.get_legend_handles_labels()
            lines2, labs2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labs1 + labs2, loc="upper left")

        ax.legend(loc="best") if proj not in ("p04", "p06") else None
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.patch.set_facecolor(BG)
        return fig


# ===========================================================================
# Sensitivity heatmap
# ===========================================================================
with ui.card():
    ui.card_header("Parameter Sensitivity Heatmap")

    @render.plot(height=380)
    def heatmap_plot():
        _apply_rc()
        proj = _current_project()
        df   = _current_df()
        year = input.year()
        fig, ax = plt.subplots(figsize=(10, 4))

        if df is None or len(df) == 0:
            _no_data_plot(ax)
            fig.patch.set_facecolor(BG)
            return fig

        df_yr = df[df["year"] == int(year)] if "year" in df.columns else df

        if proj == "p01":
            if not all(c in df_yr.columns for c in ["pv_kwp", "bess_kwh", "out_net_cost_eur"]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            pivot = df_yr.groupby(["pv_kwp", "bess_kwh"])["out_net_cost_eur"].mean().unstack(fill_value=0)
            im = ax.imshow(pivot.values, cmap="RdYlGn_r", aspect="auto")
            ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels([f"{c:.1f}" for c in pivot.columns])
            ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels([f"{r:.1f}" for r in pivot.index])
            ax.set_xlabel("BESS Capacity (kWh)"); ax.set_ylabel("PV Capacity (kWp)")
            ax.set_title("Net Annual Cost Heatmap (EUR)")
            plt.colorbar(im, ax=ax)

        elif proj == "p02":
            if not all(c in df_yr.columns for c in ["turb_mw_unit", "wear_eur", "out_revenue_eur"]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            pivot = df_yr.groupby(["turb_mw_unit", "wear_eur"])["out_revenue_eur"].mean().unstack(fill_value=0)
            im = ax.imshow(pivot.values, cmap="YlGn", aspect="auto")
            ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels([str(c) for c in pivot.columns])
            ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels([str(r) for r in pivot.index])
            ax.set_xlabel("Wear Penalty (EUR)"); ax.set_ylabel("Turbine MW/unit")
            ax.set_title("Revenue Heatmap (EUR)")
            plt.colorbar(im, ax=ax)

        elif proj == "p03":
            if not all(c in df_yr.columns for c in ["dim_limit_kw", "sessions_per_day", "out_savings_pct"]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            pivot = df_yr.groupby(["dim_limit_kw", "sessions_per_day"])["out_savings_pct"].mean().unstack(fill_value=0)
            im = ax.imshow(pivot.values, cmap="YlGn", aspect="auto")
            ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels([str(c) for c in pivot.columns])
            ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels([str(r) for r in pivot.index])
            ax.set_xlabel("Sessions / Day"); ax.set_ylabel("Dimming Limit (kW)")
            ax.set_title("Cost Savings % Heatmap")
            plt.colorbar(im, ax=ax)

        elif proj == "p04":
            if not all(c in df_yr.columns for c in ["n_members", "shared_pv_kwp", "out_welfare_eur_per_week"]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            pivot = df_yr.groupby(["n_members", "shared_pv_kwp"])["out_welfare_eur_per_week"].mean().unstack(fill_value=0)
            im = ax.imshow(pivot.values, cmap="Blues", aspect="auto")
            ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels([str(c) for c in pivot.columns])
            ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels([str(r) for r in pivot.index])
            ax.set_xlabel("Shared PV (kWp)"); ax.set_ylabel("Members")
            ax.set_title("Weekly Welfare Surplus Heatmap (EUR)")
            plt.colorbar(im, ax=ax)

        elif proj == "p05":
            if not all(c in df_yr.columns for c in ["poc_mw", "bess_mwh", "out_revenue_eur"]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            pivot = df_yr.groupby(["poc_mw", "bess_mwh"])["out_revenue_eur"].mean().unstack(fill_value=0)
            im = ax.imshow(pivot.values, cmap="plasma", aspect="auto")
            ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels([str(c) for c in pivot.columns])
            ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels([str(r) for r in pivot.index])
            ax.set_xlabel("BESS (MWh)"); ax.set_ylabel("POC Limit (MW)")
            ax.set_title("Revenue Heatmap (EUR)")
            plt.colorbar(im, ax=ax)

        elif proj == "p06":
            if not all(c in df_yr.columns for c in ["tau", "sigma_base", "out_net_gain_30d_eur"]):
                _no_data_plot(ax); fig.patch.set_facecolor(BG); return fig
            pivot = df_yr.groupby(["tau", "sigma_base"])["out_net_gain_30d_eur"].mean().unstack(fill_value=0)
            im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto")
            ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels([f"{c:.3f}" for c in pivot.columns])
            ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels([f"{r:.2f}" for r in pivot.index])
            ax.set_xlabel("Forecast Sigma"); ax.set_ylabel("Tau (risk aversion)")
            ax.set_title("Net 30d Trading Gain Heatmap (EUR)")
            plt.colorbar(im, ax=ax)

        fig.tight_layout()
        fig.patch.set_facecolor(BG)
        return fig


def _no_data_plot(ax):
    ax.text(0.5, 0.5,
            "Simulation results not yet available.\n"
            "Run: python scripts/run_overnight_simulations.py --full",
            ha="center", va="center", transform=ax.transAxes,
            color=SUB, fontsize=10, wrap=True)
    ax.set_axis_off()

"""Battery and inverter sizing grid experiment.

Evaluates combinations of battery capacity (kWh) and inverter power (kW)
under 15-minute and hourly day-ahead market time units to identify optimal
sizing, payback, and ROCE.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from ..baselines.lp_fast import solve_window_fast
from ..config import RunConfig, SiteConfig
from ..market import tariff
from ..model import site
from ..reporting.economics import compute_investment_metrics
from ..scenarios import EXTENSION_RUN
from ..thesis import COSTS_OPTIMIZED, THESIS_SITE


DEFAULT_CAPACITIES = (5.0, 7.5, 9.37, 12.0, 15.0)
DEFAULT_INVERTERS = (3.0, 4.6, 5.63, 7.5)


def run_sizing_grid(df: pd.DataFrame,
                    capacities: tuple[float, ...] = DEFAULT_CAPACITIES,
                    inverters: tuple[float, ...] = DEFAULT_INVERTERS,
                    run: RunConfig = EXTENSION_RUN,
                    out_dir: str | Path | None = None) -> pd.DataFrame:
    """Evaluate battery capacity and inverter power combinations."""
    dt = run.dt
    load, pv = df["load_kw"].to_numpy(), df["pv_kw"].to_numpy()
    spot = df["spot_eur_per_kwh"].to_numpy()
    pi = tariff.import_price(spot, df.index, run.tariff)
    pe = tariff.export_price(spot, run.tariff)

    # Base case: no battery
    nobat = site.simulate(np.zeros(len(load)), load, pv, dt, run.site)
    cost_nobat = float(np.sum((nobat["p_imp"] * pi - nobat["p_exp"] * pe) * dt))

    rows = []
    total_hours = len(df) * dt
    annual_multiplier = 8760.0 / max(total_hours, 1.0)

    for e_cap in capacities:
        for p_inv in inverters:
            # Custom site config for this grid cell
            eta = (0.95 ** 0.5) * 0.97
            s_min, s_max = 0.05 * e_cap, 0.95 * e_cap
            cfg = SiteConfig(
                e_bess=e_cap, soc_min=s_min, soc_max=s_max, soc_init=0.5 * e_cap,
                eta_c=eta, eta_d=eta, p_inv=p_inv,
                p_imp_max=1e6, p_exp_max=1e6,
                c_deg=run.site.c_deg,
                wear_basis="discharge",
            )

            sol = solve_window_fast(load, pv, pi, pe, dt, cfg, cfg.soc_init)
            if sol is None:
                continue

            sim = site.simulate(sol["p_bat"], load, pv, dt, cfg)
            cost_eval = float(np.sum((sim["p_imp"] * pi - sim["p_exp"] * pe) * dt + sim["wear"] * cfg.c_deg))
            cycles_eval = float(np.sum(sim["p_dis"]) * dt / cfg.usable_kwh)

            ann_nobat = cost_nobat * annual_multiplier
            ann_bat = cost_eval * annual_multiplier
            ann_cycles = cycles_eval * annual_multiplier

            metrics = compute_investment_metrics(ann_nobat, ann_bat, cfg, COSTS_OPTIMIZED, annual_cycles=ann_cycles)

            rows.append({
                "battery_kwh": e_cap,
                "inverter_kw": p_inv,
                "capex_eur": metrics.capex_eur,
                "annual_cost_eur": ann_bat,
                "annual_savings_eur": metrics.annual_savings_eur,
                "annual_cycles": ann_cycles,
                "simple_payback_years": metrics.simple_payback_years,
                "roce_pct": metrics.roce_pct,
                "opt_profit_eur": metrics.opt_profit_annual_eur,
            })

    res_df = pd.DataFrame(rows)
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        res_df.round(2).to_csv(out / "sizing_grid.csv", index=False)

    return res_df

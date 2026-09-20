"""Battery and inverter sizing: what should the household actually buy?

For every combination of battery capacity and inverter power the site is operated over the
same out-of-sample period as every other result here (2025-01 to 2026-09, trained on 2024)
and turned into payback and return on the marginal investment.

Two controllers are reported per configuration, because the answer depends on who operates
the battery:

  b3  the deployable rolling controller, with published prices only, the day-ahead PV
      forecast, the standard-profile load forecast and terminal values fitted on 2024.
      These are the numbers a buyer would actually see.
  b2  perfect foresight over the whole period: the ceiling, not attainable.

Reporting only b2 would overstate the return of every configuration.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from ..baselines.lp_fast import solve_window_fast
from ..baselines.rolling import InformationModel, b3_rolling_realistic, fit_terminal_values
from ..config import RunConfig, SiteConfig
from ..market import tariff
from ..model import site
from ..reporting.economics import compute_investment_metrics
from ..scenarios import EXTENSION_RUN
from ..thesis import COSTS_OPTIMIZED

LOCAL_TZ = "Europe/Berlin"
DEFAULT_CAPACITIES = (5.0, 7.5, 9.37, 12.0, 15.0)
DEFAULT_INVERTERS = (3.0, 4.6, 5.63, 7.5)


def _cell_config(e_cap: float, p_inv: float, run: RunConfig) -> SiteConfig:
    eta = (0.95 ** 0.5) * 0.97
    return SiteConfig(
        e_bess=e_cap, soc_min=0.05 * e_cap, soc_max=0.95 * e_cap, soc_init=0.5 * e_cap,
        eta_c=eta, eta_d=eta, p_inv=p_inv, p_imp_max=1e6, p_exp_max=1e6,
        c_deg=run.site.c_deg, wear_basis="discharge")


def _prices_of(df: pd.DataFrame, run: RunConfig):
    spot = df["spot_eur_per_kwh"].to_numpy()
    return tariff.import_price(spot, df.index, run.tariff), tariff.export_price(spot, run.tariff)


def _cost(res: dict, pi, pe, cfg: SiteConfig, dt: float) -> float:
    return float(np.sum((res["p_imp"] * pi - res["p_exp"] * pe) * dt + res["wear"] * cfg.c_deg))


def _one_cell(e_cap: float, p_inv: float, dataset_path: str, controller: str,
              train=("2024-01-01", "2025-01-01"), test_start: str = "2025-01-01") -> dict:
    run = EXTENSION_RUN
    df = pd.read_parquet(dataset_path)
    t0, t1, ts = (pd.Timestamp(x).tz_localize(LOCAL_TZ) for x in (*train, test_start))
    tr, te = df[(df.index >= t0) & (df.index < t1)], df[df.index >= ts]
    cfg = _cell_config(e_cap, p_inv, run)
    cell_run = run.with_(site=cfg)
    dt = run.dt
    pi, pe = _prices_of(te, run)
    load, pv = te["load_kw"].to_numpy(), te["pv_kw"].to_numpy()

    if controller == "b3":
        tv = fit_terminal_values(tr, *_prices_of(tr, run), cell_run)
        res = b3_rolling_realistic(te, pi, pe, cell_run, InformationModel(), terminal=tv)
    else:
        sol = solve_window_fast(load, pv, pi, pe, dt, cfg, cfg.soc_init)
        if sol is None:
            return {}
        res = site.simulate(sol["p_bat"], load, pv, dt, cfg)

    nobat = site.simulate(np.zeros(len(load)), load, pv, dt, cfg)
    years = len(te) * dt / 8760.0
    ann = lambda x: x / years
    metrics = compute_investment_metrics(
        ann(_cost(nobat, pi, pe, cfg, dt)), ann(_cost(res, pi, pe, cfg, dt)), cfg,
        COSTS_OPTIMIZED, annual_cycles=ann(float(np.sum(res["p_dis"]) * dt / cfg.usable_kwh)))
    return {
        "controller": controller, "battery_kwh": e_cap, "inverter_kw": p_inv,
        "capex_eur": metrics.capex_eur,
        "annual_cost_eur": ann(_cost(res, pi, pe, cfg, dt)),
        "annual_savings_eur": metrics.annual_savings_eur,
        "annual_cycles": metrics.cycles_per_year,
        "simple_payback_years": metrics.simple_payback_years,
        "roce_pct": metrics.roce_pct,
        "opt_profit_eur": metrics.opt_profit_annual_eur,
    }


def run_sizing_grid(dataset: str | Path | pd.DataFrame,
                    capacities: tuple[float, ...] = DEFAULT_CAPACITIES,
                    inverters: tuple[float, ...] = DEFAULT_INVERTERS,
                    controllers: tuple[str, ...] = ("b3", "b2"),
                    workers: int = 6, out_dir: str | Path | None = None,
                    progress: bool = True, **cell_kwargs) -> pd.DataFrame:
    """Sizing grid for each controller. Rows are written once all cells are done.

    `dataset` is a parquet path, or a frame, which is then spilled to a temporary file so
    the worker processes can read it.
    """
    import tempfile

    tmp = None
    if isinstance(dataset, pd.DataFrame):
        tmp = Path(tempfile.mkdtemp()) / "sizing_input.parquet"
        dataset.to_parquet(tmp)
        dataset = tmp
    cells = [(e, p, str(dataset), c)
             for c in controllers for e in capacities for p in inverters]
    if progress:
        print(f"{len(cells)} sizing runs on {workers} workers", flush=True)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_one_cell, *zip(*cells)))
    else:
        rows = [_one_cell(*c) for c in cells]

    res = pd.DataFrame([r for r in rows if r])
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        res.round(2).to_csv(out / "sizing_grid.csv", index=False)
    return res

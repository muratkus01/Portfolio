"""Experiment: how much of the perfect-foresight value does a realistic B3 capture?

Protocol
  train  2024 (only used to fit the terminal value)
  test   2025-01-01 to the end of the data (2026-09-17), simulated continuously, reported
         per month
  B1     rule-based self-consumption
  B2     perfect-foresight LP over the whole test period (the ceiling)
  B3     rolling LP-MPC with the realistic information set, in variants that separate the
         cost of the price-limited horizon (perfect forecasts) from the cost of forecast
         error (day-ahead NWP forecasts), and compare terminal values

Headline metric per controller: share of the B1 -> B2 headroom captured,
(cost_B1 - cost_X) / (cost_B1 - cost_B2).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..baselines.ladder import b1_rule_based
from ..baselines.lp_fast import solve_window_fast
from ..baselines.rolling import InformationModel, b3_rolling_realistic, fit_terminal_values
from ..config import RunConfig
from ..market import settlement, tariff
from ..model import site
from ..scenarios import EXTENSION_RUN

LOCAL_TZ = "Europe/Berlin"


@dataclass(frozen=True)
class Variant:
    name: str
    info: InformationModel
    terminal: str                         # "none" | "blend" | "fitted"


DEFAULT_VARIANTS = (
    Variant("B3 perfect fc, no TV", InformationModel(pv_forecast="perfect"), "none"),
    Variant("B3 perfect fc, fitted TV", InformationModel(pv_forecast="perfect"), "fitted"),
    Variant("B3 NWP fc, no TV", InformationModel(), "none"),
    Variant("B3 NWP fc, blend TV", InformationModel(), "blend"),
    Variant("B3 NWP fc, fitted TV", InformationModel(), "fitted"),
)


def _prices(df: pd.DataFrame, run: RunConfig):
    s = df["spot_eur_per_kwh"].to_numpy()
    return tariff.import_price(s, df.index, run.tariff), tariff.export_price(s, run.tariff)


def _step_costs(res: dict, pi: np.ndarray, pe: np.ndarray, run: RunConfig) -> np.ndarray:
    return (res["p_imp"] * pi - res["p_exp"] * pe) * run.dt + res["wear"] * run.site.c_deg


def run_rolling_eval(df: pd.DataFrame, run: RunConfig = EXTENSION_RUN,
                     train=("2024-01-01", "2025-01-01"), test_start: str = "2025-01-01",
                     variants=DEFAULT_VARIANTS, out_dir: str | Path | None = None,
                     progress: bool = True) -> dict[str, pd.DataFrame]:
    """Returns {"monthly": cost per month and controller, "summary": totals and capture}."""
    t0 = pd.Timestamp(train[0]).tz_localize(LOCAL_TZ)
    t1 = pd.Timestamp(train[1]).tz_localize(LOCAL_TZ)
    ts = pd.Timestamp(test_start).tz_localize(LOCAL_TZ)
    tr = df[(df.index >= t0) & (df.index < t1)]
    te = df[df.index >= ts]

    tv = fit_terminal_values(tr, *_prices(tr, run), run)
    pi, pe = _prices(te, run)
    load, pv = te["load_kw"].to_numpy(), te["pv_kw"].to_numpy()

    costs: dict[str, np.ndarray] = {}
    extra: dict[str, dict] = {}
    b1 = b1_rule_based(load, pv, run.dt, run)
    costs["B1 rule-based"] = _step_costs(b1, pi, pe, run)
    b2 = solve_window_fast(load, pv, pi, pe, run.dt, run.site, run.site.soc_init)
    b2 = site.simulate(b2["p_bat"], load, pv, run.dt, run.site)
    costs["B2 perfect foresight"] = _step_costs(b2, pi, pe, run)
    for res, name in ((b1, "B1 rule-based"), (b2, "B2 perfect foresight")):
        extra[name] = {"cycles": res["p_dis"].sum() * run.dt / run.site.usable_kwh,
                       "violations": sum(site.check_feasible(res, run.dt, run.site).values())}

    for v in variants:
        if progress:
            print(f"  running {v.name} ...", flush=True)
        term = tv if v.terminal == "fitted" else v.terminal
        r = b3_rolling_realistic(te, pi, pe, run, v.info, terminal=term)
        costs[v.name] = _step_costs(r, pi, pe, run)
        extra[v.name] = {"cycles": r["p_dis"].sum() * run.dt / run.site.usable_kwh,
                         "violations": sum(site.check_feasible(r, run.dt, run.site).values()),
                         "mean_solve_ms": 1000 * float(np.mean(r["solve_time_s"])),
                         "mean_horizon_h": float(np.mean(r["horizon_steps"])) * run.dt}

    month = te.index.tz_convert(LOCAL_TZ).strftime("%Y-%m")
    monthly = pd.DataFrame(costs, index=te.index).groupby(month).sum()
    head = monthly["B1 rule-based"] - monthly["B2 perfect foresight"]
    capture = monthly.rsub(monthly["B1 rule-based"], axis=0).div(head, axis=0)

    tot = monthly.sum()
    summary = pd.DataFrame({
        "net cost EUR": tot,
        "capture of B1->B2 %": 100 * (tot["B1 rule-based"] - tot) / (
            tot["B1 rule-based"] - tot["B2 perfect foresight"]),
    })
    summary = summary.join(pd.DataFrame(extra).T)
    summary.attrs["terminal_values"] = tv

    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        monthly.round(4).to_csv(out / "rolling_eval_monthly_cost.csv")
        (100 * capture).round(1).to_csv(out / "rolling_eval_monthly_capture.csv")
        summary.round(4).to_csv(out / "rolling_eval_summary.csv")
        pd.Series(tv, name="terminal_value_eur_per_kwh").to_csv(
            out / "rolling_eval_terminal_values.csv")
    return {"monthly": monthly, "capture": 100 * capture, "summary": summary}

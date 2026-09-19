"""Experiment: what is the quarter-hourly day-ahead market worth to a home battery?

Since 2025-10-01 the EPEX day-ahead auction clears 96 quarter-hour products per day instead of
24 hourly ones. For the same household, battery and controller, three cases are compared on
the period after the switch:

  (a) "15-min"          the controller sees the real quarter-hour prices; settled on them
  (b) "hourly signal"   the controller sees the hourly mean of those prices (what an
                        hourly product would have signalled); settled on the real
                        quarter-hour prices
  (c) "hourly model"    the thesis-style model: load, PV and prices averaged to hours, the
                        dispatch optimised and settled hourly. This is what an hourly study
                        would REPORT; it is not a controller that runs on the real system.

(b) - (a) is the value of reacting to quarter-hour prices. (c) - (a) is the error an hourly
model makes about the same household today. B1 is price-blind, so its (a) and (b) coincide;
its (c) shows the pure averaging effect of hourly energy balances.

The hourly mean of four quarter-hour prices is an approximation of the counterfactual hourly
price. Bidding behaviour would have differed under hourly products, so (b) measures the
value of the finer SIGNAL, not a market counterfactual.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..baselines.ladder import b1_rule_based
from ..baselines.lp_fast import solve_window_fast
from ..baselines.rolling import InformationModel, b3_rolling_realistic, fit_terminal_values
from ..config import RunConfig
from ..market import tariff
from ..model import site
from ..scenarios import EXTENSION_RUN
from .rolling_eval import _prices, _step_costs

LOCAL_TZ = "Europe/Berlin"
SWITCH = "2025-10-01"


def _hourly_mean_per_quarter(spot: pd.Series) -> pd.Series:
    """Each quarter-hour carries the mean of its local clock hour."""
    return spot.groupby(spot.index.floor("1h")).transform("mean")


def _hourly_frame(df: pd.DataFrame) -> pd.DataFrame:
    return df[["load_kw", "pv_kw", "spot_eur_per_kwh"]].resample("1h").mean()


def run_resolution_study(df: pd.DataFrame, run: RunConfig = EXTENSION_RUN,
                         start: str = SWITCH, train=("2024-01-01", "2025-01-01"),
                         out_dir: str | Path | None = None, progress: bool = True
                         ) -> dict[str, pd.DataFrame]:
    ts = pd.Timestamp(start).tz_localize(LOCAL_TZ)
    t0, t1 = (pd.Timestamp(x).tz_localize(LOCAL_TZ) for x in train)
    tr = df[(df.index >= t0) & (df.index < t1)]
    te = df[df.index >= ts].copy()
    tv = fit_terminal_values(tr, *_prices(tr, run), run)

    pi15, pe15 = _prices(te, run)
    hourly_spot = _hourly_mean_per_quarter(te["spot_eur_per_kwh"])
    pih = tariff.import_price(hourly_spot.to_numpy(), te.index, run.tariff)
    peh = tariff.export_price(hourly_spot.to_numpy(), run.tariff)
    load, pv = te["load_kw"].to_numpy(), te["pv_kw"].to_numpy()
    settle15 = lambda r: _step_costs(r, pi15, pe15, run)

    costs: dict[str, np.ndarray] = {}
    say = (lambda m: print(f"  {m}", flush=True)) if progress else (lambda m: None)

    # B1: price-blind, one dispatch
    b1 = b1_rule_based(load, pv, run.dt, run)
    costs["B1 | 15-min"] = settle15(b1)

    # B2: perfect foresight on each price signal, both settled at quarter-hour prices
    for label, (pi, pe) in {"15-min": (pi15, pe15), "hourly signal": (pih, peh)}.items():
        say(f"B2 {label}")
        sol = solve_window_fast(load, pv, pi, pe, run.dt, run.site, run.site.soc_init)
        costs[f"B2 | {label}"] = settle15(site.simulate(sol["p_bat"], load, pv, run.dt,
                                                        run.site))

    # B3: the deployable controller on each price signal, settled at quarter-hour prices
    for label, (pi, pe) in {"15-min": (pi15, pe15), "hourly signal": (pih, peh)}.items():
        say(f"B3 {label}")
        tmp = te.copy()
        tmp["spot_eur_per_kwh"] = te["spot_eur_per_kwh"] if label == "15-min" else hourly_spot
        r = b3_rolling_realistic(tmp, pi, pe, run, InformationModel(), terminal=tv)
        costs[f"B3 | {label}"] = settle15(r)

    step_costs = pd.DataFrame(costs, index=te.index)

    # (c) the hourly model: everything averaged to hours, optimised and settled hourly
    h = _hourly_frame(te)
    hrun = run.with_(dt=1.0)
    hpi = tariff.import_price(h["spot_eur_per_kwh"].to_numpy(), h.index, run.tariff)
    hpe = tariff.export_price(h["spot_eur_per_kwh"].to_numpy(), run.tariff)
    hl, hp = h["load_kw"].to_numpy(), h["pv_kw"].to_numpy()
    model = {"B1 | hourly model": _step_costs(b1_rule_based(hl, hp, 1.0, hrun), hpi, hpe, hrun)}
    sol = solve_window_fast(hl, hp, hpi, hpe, 1.0, hrun.site, hrun.site.soc_init)
    model["B2 | hourly model"] = _step_costs(site.simulate(sol["p_bat"], hl, hp, 1.0, hrun.site),
                                             hpi, hpe, hrun)
    model_costs = pd.DataFrame(model, index=h.index)

    month = lambda idx: idx.tz_convert(LOCAL_TZ).strftime("%Y-%m")
    monthly = step_costs.groupby(month(te.index)).sum().join(
        model_costs.groupby(month(h.index)).sum())
    tot = monthly.sum()

    rows = []
    for ctrl in ("B1", "B2", "B3"):
        a = tot.get(f"{ctrl} | 15-min")
        b = tot.get(f"{ctrl} | hourly signal", a)
        c = tot.get(f"{ctrl} | hourly model", np.nan)
        rows.append({"controller": ctrl, "cost 15-min EUR": a, "cost hourly signal EUR": b,
                     "value of 15-min signal EUR": b - a, "hourly model reports EUR": c,
                     "hourly model error EUR": c - a})
    summary = pd.DataFrame(rows).set_index("controller")
    months = len(monthly)
    summary["value of 15-min signal EUR/yr"] = summary["value of 15-min signal EUR"] * 12 / months
    headroom = tot["B1 | 15-min"] - tot["B2 | 15-min"]
    summary["value as % of B1->B2 headroom"] = 100 * summary["value of 15-min signal EUR"] / headroom

    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        monthly.round(4).to_csv(out / "resolution_monthly_cost.csv")
        summary.round(4).to_csv(out / "resolution_summary.csv")
    return {"monthly": monthly, "summary": summary}

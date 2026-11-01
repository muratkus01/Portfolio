"""The real-data commands, `build-data`, `backtest` and `derive`; every table lands in reports/.

    build-data                  fetch and cache every input, write reports/data_coverage.csv
    backtest --phase validation fit on train, tune on validation, write reports/validation/
                                and the frozen settings reports/frozen_config.json
    backtest --phase test       refit on train + validation with the frozen settings and write
                                reports/test/ for the 2026 window, once
    derive                      rebuild the tables added after a run from its committed CSVs
"""
from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import (
    error_alignment,
    liquidity_sweep,
    quality_vs_value,
    quality_vs_value_by_seed,
)
from .config import MarketConfig, RunConfig
from .data import build_dataset
from .evaluate import (
    common_mask,
    daily_net,
    fit_ladder,
    load_ladder,
    market_prices,
    newsvendor_tau,
    save_ladder,
    score_ladder,
    tune_deadband,
    value_of_forecast,
)
from .features import SPLITS
from .weather import fetch_previous_runs, to_quarter_hours

PROJECT = Path(__file__).resolve().parents[2]
DATA = PROJECT / "data"
REPORTS = PROJECT / "reports"
PERIOD = ("2024-01-01", "2026-10-31")


def dataset_path() -> Path:
    return DATA / "processed" / f"dataset_{PERIOD[0]}_{PERIOD[1]}.parquet"


def weather_path() -> Path:
    return DATA / "processed" / f"weather_hourly_{PERIOD[0]}_{PERIOD[1]}.parquet"


def cmd_build_data(args) -> None:
    df = build_dataset(*PERIOD, RunConfig(), DATA / "raw" / "netztransparenz")
    hourly = fetch_previous_runs(*PERIOD, DATA / "raw" / "open_meteo")
    dataset_path().parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dataset_path())
    hourly.to_parquet(weather_path())
    weather_q = to_quarter_hours(hourly, df.index)
    REPORTS.mkdir(parents=True, exist_ok=True)
    data_coverage(df, weather_q).to_csv(REPORTS / "data_coverage.csv", index=False)
    alignment = clock_alignment(df, weather_q)
    alignment.round(4).to_csv(REPORTS / "data_alignment.csv", index=False)
    peaks = alignment.loc[alignment.groupby("pair")["correlation"].idxmax()]
    print(peaks.to_string(index=False))


def clock_alignment(df: pd.DataFrame, weather_q: pd.DataFrame,
                    max_lag: int = 8) -> pd.DataFrame:
    """Correlation of the day-1 weather forecast with measured output at shifted lags.

    Both series are labelled by interval start, so the correlation should peak at lag 0. A
    peak one hour off would be a label error of the kind P01's HTW clock bug was.
    """
    sites = sorted({c.split("|")[0] for c in weather_q.columns} - {"OFF_NORTH", "OFF_BALTIC"})
    ghi = weather_q[[f"{s}|shortwave_radiation|d1" for s in sites]].mean(axis=1)
    wind = weather_q[[f"{s}|wind_speed_100m|d1" for s in sites]].mean(axis=1) ** 3
    rows = []
    for pair, forecast, measured in (("ghi vs pv_cf", ghi, df["pv_cf"]),
                                     ("wind speed cubed vs wind_cf", wind, df["wind_cf"])):
        for lag in range(-max_lag, max_lag + 1):
            rows.append({"pair": pair, "lag_steps": lag,
                         "correlation": float(forecast.corr(measured.shift(-lag)))})
    return pd.DataFrame(rows)


def data_coverage(df: pd.DataFrame, weather_q: pd.DataFrame) -> pd.DataFrame:
    """First and last valid step of every series, and its missing share in each split."""
    frame = pd.concat([df, weather_q], axis=1)
    rows = []
    for col in frame.columns:
        s = frame[col]
        row = {"series": col, "first_valid": s.first_valid_index(),
               "last_valid": s.last_valid_index()}
        for name, (a, b) in SPLITS.items():
            part = s[(s.index >= pd.Timestamp(a, tz="UTC")) & (s.index < pd.Timestamp(b, tz="UTC"))]
            row[f"missing_{name}"] = round(float(part.isna().mean()), 5) if len(part) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def cmd_backtest(args) -> None:
    out = REPORTS / args.phase
    if args.phase == "test" and (out / "forecast_by_stage.csv").exists() and not args.force:
        raise SystemExit(f"{out} exists: the test window is evaluated once. "
                         "Pass --force only to repair a broken run, and say so in the README.")
    df = pd.read_parquet(dataset_path())
    weather_q = to_quarter_hours(pd.read_parquet(weather_path()), df.index)
    run = RunConfig(market=MarketConfig(id_spread_eur_mwh=args.spread))
    frozen = (json.loads((REPORTS / "frozen_config.json").read_text())
              if args.phase == "test" else None)

    ladder, made_by = _forecasts(args, df, weather_q, run)
    truth = df["truth_mw"].to_numpy(float)
    mask = common_mask(ladder, truth)
    priced = df["rebap"].notna().to_numpy() & df["price_da"].notna().to_numpy()
    if (mask & priced).any():
        # one set of steps for every table: forecasts are scored where they are also valued
        mask &= priced
    out.mkdir(parents=True, exist_ok=True)
    scores = score_ladder(ladder, truth, mask, run.dt)
    scores.round(4).to_csv(out / "forecast_by_stage.csv", index=False)
    print(scores.pivot(index="rung", columns="stage", values="pinball")
          .to_string(float_format=lambda x: f"{x:.3f}"))

    market = _market_value(ladder, df, mask, run, args.phase, frozen, out, scores)
    meta = _run_meta(args, int(mask.sum()), market)
    meta.update(forecasts_made_by=made_by["git_commit"], f3_iterations=made_by["f3_iterations"])
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    if args.phase == "validation" and market is not None:
        frozen = {"deadband_k": market["deadband_k"], "made_by": meta["git_commit"]}
        (REPORTS / "frozen_config.json").write_text(json.dumps(frozen, indent=2))


def _forecasts(args, df: pd.DataFrame, weather_q: pd.DataFrame, run: RunConfig):
    """Fit the ladder and cache its forecasts, or with --reuse-forecasts read the cache.

    Reuse lets the market part rerun in a minute after a fix there; run_meta.json then
    names the commit that made the forecasts as well as the one that valued them.
    """
    cache = DATA / "processed" / f"ladder_{args.phase}.npz"
    made_path = cache.with_suffix(".json")
    if args.reuse_forecasts:
        return load_ladder(cache, len(df)), json.loads(made_path.read_text())
    ladder = fit_ladder(df, weather_q, run, args.phase, tuple(args.seeds))
    save_ladder(ladder, cache)
    made_by = {"git_commit": _git("rev-parse", "--short", "HEAD"),
               "source_changes_uncommitted": bool(_git("status", "--porcelain", "--", "src")),
               "f3_iterations": ladder.f3_iterations,
               "created_utc": datetime.now(timezone.utc).isoformat()}
    made_path.write_text(json.dumps(made_by, indent=2))
    return ladder, made_by


def _market_value(ladder, df: pd.DataFrame, mask: np.ndarray, run: RunConfig, phase: str,
                  frozen: dict | None, out: Path, scores: pd.DataFrame) -> dict | None:
    """Settle every rung and policy at the real prices; skipped until reBAP is present."""
    priced = mask & df["rebap"].notna().to_numpy()
    if not priced.any():
        print("\nNo reBAP in the dataset yet: market value skipped, forecast scores written.")
        return None
    tau = newsvendor_tau(df, phase)
    k = (tune_deadband(ladder, df, priced, run, tau) if phase == "validation"
         else frozen["deadband_k"])
    totals, daily = daily_net(ladder, df, priced, run, k, tau)
    totals.round(2).to_csv(out / "policies.csv", index=False)
    flat = daily.copy()
    flat.columns = [f"{r} | {p}" for r, p in daily.columns]
    flat.round(2).to_csv(out / "daily_net.csv", index_label="delivery_day")
    value_of_forecast(daily).round(4).to_csv(out / "value_of_forecast.csv", index=False)
    error_alignment(ladder, df, priced, run).round(4).to_csv(out / "error_alignment.csv",
                                                             index=False)
    quality_vs_value(scores, totals).round(4).to_csv(out / "quality_vs_value.csv", index=False)
    quality_vs_value_by_seed(scores, totals).round(4).to_csv(
        out / "quality_vs_value_by_seed.csv", index=False)
    liquidity_sweep(ladder, df, priced, run, k, tau).round(2).to_csv(out / "liquidity.csv",
                                                                     index=False)
    print("\n" + totals.pivot(index="rung", columns="policy", values="net_revenue")
          .to_string(float_format=lambda x: f"{x:,.0f}"))
    fallback = market_prices(df)["id_aep_missing"] & priced
    return {"deadband_k": k, "newsvendor_tau": tau, "priced_steps": int(priced.sum()),
            "id_aep_fallback_steps": int(fallback.sum())}


def cmd_derive(args) -> None:
    """Rebuild the tables that follow from a run's committed CSVs, for tables added after it.

    It reads forecast_by_stage.csv and policies.csv and refits or revalues nothing, so the
    test window stays evaluated once.
    """
    for phase in args.phases:
        out = REPORTS / phase
        scores = pd.read_csv(out / "forecast_by_stage.csv")
        totals = pd.read_csv(out / "policies.csv")
        path = out / "quality_vs_value_by_seed.csv"
        quality_vs_value_by_seed(scores, totals).round(4).to_csv(path, index=False)
        print(f"wrote {path.relative_to(PROJECT)}")


def _git(*cmd: str) -> str:
    # check=False: outside a git checkout the fields stay empty instead of failing the run
    res = subprocess.run(["git", *cmd], cwd=PROJECT, capture_output=True, text=True,
                         check=False)
    return res.stdout.strip()


def _run_meta(args, steps: int, market: dict | None) -> dict:
    import lightgbm

    return {"phase": args.phase, "splits": SPLITS, "seeds": list(args.seeds),
            "id_spread_eur_mwh": args.spread, "evaluated_steps": steps, "market": market,
            "git_commit": _git("rev-parse", "--short", "HEAD"),
            "source_changes_uncommitted": bool(_git("status", "--porcelain", "--", "src")),
            "python": platform.python_version(), "lightgbm": lightgbm.__version__,
            "pandas": pd.__version__, "created_utc": datetime.now(timezone.utc).isoformat()}

"""Command-line entry point for the forecast-to-bid project.

    python -m f2b.cli policies  --year 2025 --days 60   compare all trading policies
    python -m f2b.cli forecast  --year 2025 --days 60   forecast scores by decision stage
    python -m f2b.cli crps-eur  --year 2025 --days 60   RQ1: does the forecast score predict EUR?
    python -m f2b.cli liquidity --year 2025 --days 60   sensitivity to the liquidity assumption

These commands run on the PARAMETRIC NOISE ablation: the forecasts are the realised series
plus hand-sized errors, and the imbalance price is synthetic.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ForecastConfig, MarketConfig, PortfolioConfig, RunConfig
from .forecast import ForecastStore, build_store, score_stage, stages_from_store
from .policies import (
    b0_day_ahead_only,
    b1_point_intraday,
    b2_perfect_speculation,
    b2_zero_imbalance,
    b3_deadband,
    imbalance_cost_asymmetry,
    newsvendor_quantile,
    nv_policy,
    rebap_series,
    settle,
)
from .stages import Stage, audit_stages, valid_steps

_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))

# Days loaded before the evaluation window. Used only to estimate the newsvendor fractile,
# so no number reported for the window is fitted on the window itself.
TRAIN_DAYS = 30


def load(year: int, days: int, run: RunConfig):
    """Real German 15-minute wind+PV generation and day-ahead prices, scaled to a portfolio."""
    import datakit

    start, end = f"{year}-01-01", f"{year + 1}-01-01"
    power = datakit.public_power(start, end)
    price = datakit.day_ahead_price(start, end)

    def pick(name):
        for c in power.columns:
            if c.lower() == name.lower():
                return power[c]
        raise KeyError(name)

    # Single source of truth from datakit. Raises ValueError on unregistered years; no silent fallback.
    cap = datakit.get_installed_capacity(year)
    wind_cf = ((pick("Wind onshore") + pick("Wind offshore")) / cap["wind"]).clip(0, 1)
    pv_cf = (pick("Solar") / cap["pv"]).clip(0, 1)
    load_mw = pick("Load")

    df = pd.DataFrame({"wind_cf": wind_cf, "pv_cf": pv_cf, "load_mw": load_mw}).dropna()
    df["price"] = price["price_eur_per_mwh"].reindex(df.index, method="ffill")
    df = df.dropna().iloc[:int(days * 24 / run.dt)]

    truth = (df["wind_cf"].to_numpy() * run.portfolio.wind_mw
             + df["pv_cf"].to_numpy() * run.portfolio.pv_mw)
    # system imbalance proxy: residual-load ramp drives the sign of the imbalance price
    resid = df["load_mw"].to_numpy() - (df["wind_cf"].to_numpy() * cap["wind"]
                                        + df["pv_cf"].to_numpy() * cap["pv"])
    return df.index, truth, df["price"].to_numpy(), np.diff(resid, prepend=resid[0])


@dataclass
class Setup:
    """One evaluation window and everything decided before it."""
    run: RunConfig
    truth: np.ndarray
    price: np.ndarray
    rebap: np.ndarray
    store: ForecastStore
    stages: list[Stage]
    mask: np.ndarray        # evaluation steps: after the training days, decided inside the data
    tau: float              # newsvendor fractile, estimated on the training days only


def _setup(args, forecast: ForecastConfig | None = None) -> Setup:
    run = RunConfig(dt=0.25, seed=args.seed, portfolio=PortfolioConfig(),
                    market=MarketConfig(id_spread_eur_mwh=args.spread))
    if forecast is not None:
        run = run.with_(forecast=forecast)
    index, truth, price, imb_signal = load(args.year, args.days + TRAIN_DAYS, run)
    n = len(truth)
    capacity = run.portfolio.wind_mw + run.portfolio.pv_mw
    max_lead = round(36 / run.dt)
    # Separate random streams, so that changing the forecast model leaves the reBAP draw alone.
    store = build_store(truth, run.forecast, run.dt, max_lead,
                        np.random.default_rng([run.seed, 0]), capacity)
    train = np.arange(n) < TRAIN_DAYS * run.steps_per_day
    rebap = rebap_series(n, price, imb_signal, run.market, np.random.default_rng([run.seed, 1]),
                         signal_scale=float(np.std(imb_signal[train])))
    stages = stages_from_store(store, index, run)
    tau = newsvendor_quantile(*imbalance_cost_asymmetry(rebap[train], price[train]))
    return Setup(run=run, truth=truth, price=price, rebap=rebap, store=store, stages=stages,
                 mask=valid_steps(stages) & ~train, tau=tau)


def _policies(s: Setup) -> dict[str, tuple]:
    capacity = s.run.portfolio.wind_mw + s.run.portfolio.pv_mw
    return {
        "B0 day-ahead only": b0_day_ahead_only(s.stages),
        "B1 intraday to median": b1_point_intraday(s.stages),
        "B3 deadband intraday": b3_deadband(s.stages),
        "NV newsvendor fractile": nv_policy(s.stages, s.tau),
        "REF zero imbalance": b2_zero_imbalance(s.truth),
        "CEIL perfect speculation": b2_perfect_speculation(s.truth, s.price, s.rebap, capacity),
    }


def _settle(s: Setup, pos: np.ndarray, trades: list[np.ndarray]) -> dict[str, float]:
    """Settle the evaluation window only. Intraday trades clear at the day-ahead price here."""
    m = s.mask
    return settle(pos[m], [t[m] for t in trades], s.truth[m], s.price[m], s.rebap[m],
                  s.price[m], s.run)


def cmd_policies(args) -> None:
    s = _setup(args)
    print(f"{int(s.mask.sum())} evaluated steps | {args.days} days of {args.year} after "
          f"{TRAIN_DAYS} training days | portfolio {s.run.portfolio.wind_mw:.0f} MW wind + "
          f"{s.run.portfolio.pv_mw:.0f} MW PV | parametric noise ablation")
    print(f"newsvendor fractile from the training days: {s.tau:.3f}")
    print(f"decision-stage audit: {audit_stages(s.stages)} forecasts read after their "
          f"decision time (must be 0)")

    rows = []
    for name, (pos, trades) in _policies(s).items():
        r = _settle(s, pos, trades)
        rows.append({"policy": name, "net_EUR": r["net_revenue"],
                     "EUR_per_MWh": r["eur_per_mwh"],
                     "imbalance_MWh": r["imbalance_mwh"],
                     "traded_MWh": r["traded_mwh"], "id_cost_EUR": r["id_cost"]})
    df = pd.DataFrame(rows)
    print("\nTrading policies")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    deployable = df[df.policy.str.match(r"B\d|NV")]
    best = deployable.loc[deployable.net_EUR.idxmax()]
    print(f"\nbest deployable policy: {best.policy} ({best.net_EUR:,.0f} EUR)")


def cmd_forecast(args) -> None:
    s = _setup(args)
    df = pd.DataFrame([score_stage(st, s.truth, s.mask, s.run.dt) for st in s.stages])
    print("\nForecast quality by decision stage (portfolio MW, parametric noise ablation)")
    cols = ["stage", "mean_lead_h", "MAE", "RMSE", "bias", "pinball", "calibration_error"]
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:,.3f}"))


def cmd_crps_eur(args) -> None:
    """RQ1 on the ablation: do forecasts ranked by their score rank the same way by EUR?"""
    rows = []
    for sigma in (0.03, 0.05, 0.08, 0.12, 0.18):
        for rho in (0.5, 0.85):
            s = _setup(args, ForecastConfig(sigma_base=sigma, rho=rho))
            da = score_stage(s.stages[0], s.truth, s.mask, s.run.dt)
            r = _settle(s, *b1_point_intraday(s.stages))
            rows.append({"sigma": sigma, "rho": rho, "DA_pinball": da["pinball"],
                         "DA_RMSE": da["RMSE"], "B1_net_EUR": r["net_revenue"]})
    df = pd.DataFrame(rows)
    print("\nForecast score vs decision value (B1, parametric noise ablation)")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    rho_s = df["DA_pinball"].rank().corr((-df["B1_net_EUR"]).rank())
    print(f"\nSpearman rank correlation, day-ahead pinball vs -EUR: {rho_s:.3f} "
          f"(1 means the score ranks the forecasts exactly as their EUR value does)")


def cmd_liquidity(args) -> None:
    """Every result is reported as a FUNCTION of the liquidity assumption, not at a point."""
    rows = []
    for spread in (0.5, 1.5, 3.0, 6.0, 12.0):
        args.spread = spread
        s = _setup(args)
        for name, (pos, tr) in (("B1 intraday to median", b1_point_intraday(s.stages)),
                                ("B3 deadband intraday", b3_deadband(s.stages)),
                                ("NV newsvendor fractile", nv_policy(s.stages, s.tau))):
            r = _settle(s, pos, tr)
            rows.append({"spread_EUR_MWh": spread, "policy": name,
                         "net_EUR": r["net_revenue"], "traded_MWh": r["traded_mwh"]})
    df = pd.DataFrame(rows)
    print("\nSensitivity to the intraday liquidity assumption")
    print(df.pivot(index="spread_EUR_MWh", columns="policy", values="net_EUR")
          .to_string(float_format=lambda x: f"{x:,.0f}"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="f2b", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("policies", cmd_policies), ("forecast", cmd_forecast),
                     ("crps-eur", cmd_crps_eur), ("liquidity", cmd_liquidity)):
        sp = sub.add_parser(name)
        sp.add_argument("--year", type=int, default=2025)
        sp.add_argument("--days", type=int, default=60)
        sp.add_argument("--spread", type=float, default=3.0)
        sp.add_argument("--seed", type=int, default=0)
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for the forecast-to-bid project.

    python -m f2b.cli policies  --year 2025 --days 60   compare all trading policies
    python -m f2b.cli forecast  --year 2025 --days 60   forecast scores by lead time
    python -m f2b.cli crps-eur  --year 2025 --days 60   RQ1: does CRPS predict EUR?
    python -m f2b.cli liquidity --year 2025 --days 60   sensitivity to the liquidity assumption
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ForecastConfig, MarketConfig, PortfolioConfig, RunConfig
from .forecast import audit_no_lookahead, build_store, score_store
from .policies import (b0_day_ahead_only, b1_point_intraday, b2_perfect_speculation,
                       b2_zero_imbalance, b3_stochastic_mpc, imbalance_cost_asymmetry,
                       nv_policy, rebap_series, settle)

_DATAKIT = Path(__file__).resolve().parents[4] / "datakit"
if str(_DATAKIT) not in sys.path:
    sys.path.insert(0, str(_DATAKIT))

INSTALLED_MW = {2024: {"wind": 72000.0, "pv": 99000.0},
                2025: {"wind": 75000.0, "pv": 110000.0}}


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

    cap = INSTALLED_MW.get(year, INSTALLED_MW[2025])
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
    return truth, df["price"].to_numpy(), np.diff(resid, prepend=resid[0])


def _setup(args):
    run = RunConfig(dt=0.25, portfolio=PortfolioConfig(),
                    market=MarketConfig(id_spread_eur_mwh=args.spread))
    truth, price, imb_signal = load(args.year, args.days, run)
    n = len(truth)
    rng = np.random.default_rng(run.seed)
    capacity = run.portfolio.wind_mw + run.portfolio.pv_mw
    max_lead = int(round(36 / run.dt))
    store = build_store(truth, run.forecast, run.dt, max_lead, rng, capacity)
    rebap = rebap_series(n, price, imb_signal, run.market, rng)
    return run, truth, price, rebap, store, n


def cmd_policies(args) -> None:
    run, truth, price, rebap, store, n = _setup(args)
    cs, cl = imbalance_cost_asymmetry(rebap, price)
    print(f"{n} steps | {args.days} days of {args.year} | portfolio "
          f"{run.portfolio.wind_mw:.0f} MW wind + {run.portfolio.pv_mw:.0f} MW PV")
    print(f"produced {np.sum(truth) * run.dt:,.0f} MWh | price mean {price.mean():.1f} "
          f"EUR/MWh | reBAP sd {rebap.std():.1f}")
    print(f"one-sided imbalance costs: short {cs:.2f}, long {cl:.2f} EUR/MWh "
          f"-> newsvendor fractile {cs / (cs + cl):.3f}")
    bad = audit_no_lookahead(store, n)
    print(f"look-ahead audit: {bad} violations (must be 0)")

    policies = {
        "B0 day-ahead only": b0_day_ahead_only(store, n, run),
        "B1 point intraday": b1_point_intraday(store, n, run),
        "NV newsvendor quantile": nv_policy(store, n, run, cs, cl),
        "B3 stochastic MPC": b3_stochastic_mpc(store, n, run, cs, cl),
        "REF zero imbalance": b2_zero_imbalance(truth, run),
        "CEIL perfect speculation": b2_perfect_speculation(
            truth, price, rebap, run, run.portfolio.wind_mw + run.portfolio.pv_mw),
    }
    rows = []
    for name, (pos, trades) in policies.items():
        r = settle(pos, trades, truth, price, rebap, price, run)
        rows.append({"policy": name, "net_EUR": r["net_revenue"],
                     "EUR_per_MWh": r["eur_per_mwh"],
                     "imbalance_MWh": r["imbalance_mwh"],
                     "traded_MWh": r["traded_mwh"], "id_cost_EUR": r["id_cost"]})
    df = pd.DataFrame(rows)
    print("\nTrading policies")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

    def g(k: str) -> float:
        return float(df.loc[df.policy == k, "net_EUR"].iloc[0])

    ref, ceil = g("REF zero imbalance"), g("CEIL perfect speculation")
    b1, b3 = g("B1 point intraday"), g("B3 stochastic MPC")
    print(f"\nzero-imbalance reference : {ref:>14,.0f} EUR")
    print(f"speculation ceiling      : {ceil:>14,.0f} EUR")
    print(f"B3 - B1                  : {b3 - b1:>+14,.0f} EUR   "
          f"(value of the deadband over always chasing the mean)")
    print(f"B3 vs zero-imbalance ref : {b3 - ref:>+14,.0f} EUR")
    print("\nNOTE: zero imbalance is NOT an upper bound. Imbalance settlement is signed, so\n"
          "deviating in the system-helping direction is PAID. B0 can beat the zero-imbalance\n"
          "reference by luck alone; only the speculation ceiling bounds revenue, and no\n"
          "balance responsible party may actually pursue it - a BRP is obliged to schedule\n"
          "its best estimate. The gap between the two measures how much of the apparent\n"
          "headroom in this problem is imbalance speculation rather than better forecasting.")


def cmd_forecast(args) -> None:
    run, truth, price, rebap, store, n = _setup(args)
    rows = [score_store(store, truth, lead)
            for lead in (1, 4, 12, 24, 48, 96)]
    df = pd.DataFrame(rows)
    df["lead_hours"] = df["lead_steps"] * run.dt
    print("\nForecast quality by lead time (portfolio MW)")
    print(df[["lead_hours", "MAE", "RMSE", "bias", "CRPS", "pinball", "calibration_error"]]
          .to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print("\nCalibration error is the mean |PIT histogram - uniform|; 0 is perfect. "
          "Sharpness without calibration is worthless, so the two are always reported together.")


def cmd_crps_eur(args) -> None:
    """RQ1: do forecasts ranked by CRPS rank the same way by EUR?"""
    run, truth, price, rebap, store, n = _setup(args)
    cs, cl = imbalance_cost_asymmetry(rebap, price)
    capacity = run.portfolio.wind_mw + run.portfolio.pv_mw

    rows = []
    for sigma in (0.03, 0.05, 0.08, 0.12, 0.18):
        for rho in (0.5, 0.85):
            fc = ForecastConfig(sigma_base=sigma, rho=rho)
            st = build_store(truth, fc, run.dt, store.max_lead,
                             np.random.default_rng(run.seed), capacity)
            sc = score_store(st, truth, 24)
            pos, trades = nv_policy(st, n, run, cs, cl)
            r = settle(pos, trades, truth, price, rebap, price, run)
            rows.append({"sigma": sigma, "rho": rho, "CRPS": sc["CRPS"],
                         "RMSE": sc["RMSE"], "net_EUR": r["net_revenue"]})
    df = pd.DataFrame(rows)
    print("\nForecast quality vs decision value (newsvendor policy, 6 h lead)")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print(f"\nSpearman rank correlation CRPS vs -EUR: "
          f"{df['CRPS'].rank().corr(( -df['net_EUR']).rank()):.3f}")
    print("A correlation well below 1 is the finding: models ranked identically by CRPS can "
          "differ materially in EUR, because the decision depends on specific regions of the "
          "predictive distribution rather than on average calibration.")


def cmd_liquidity(args) -> None:
    """Every result is reported as a FUNCTION of the liquidity assumption, not at a point."""
    rows = []
    for spread in (0.5, 1.5, 3.0, 6.0, 12.0):
        args.spread = spread
        run, truth, price, rebap, store, n = _setup(args)
        cs, cl = imbalance_cost_asymmetry(rebap, price)
        for name, (pos, tr) in (("B1 point intraday", b1_point_intraday(store, n, run)),
                                ("NV newsvendor", nv_policy(store, n, run, cs, cl)),
                                ("B3 stochastic MPC", b3_stochastic_mpc(store, n, run, cs, cl))):
            r = settle(pos, tr, truth, price, rebap, price, run)
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
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

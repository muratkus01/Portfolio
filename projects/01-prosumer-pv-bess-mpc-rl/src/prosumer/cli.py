"""Command-line entry point.

    python -m prosumer.cli ladder   --data <csv>            run B1/B2/B3 at 15 min
    python -m prosumer.cli legacy   --data <csv>            reproduce the original MILP
    python -m prosumer.cli resolution --data <csv>          60-min vs 15-min bias study
    python -m prosumer.cli modules  --data <csv>            s14a module comparison
    python -m prosumer.cli fetch    --start .. --end ..     download DE-LU spot prices
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .baselines.ladder import gap_closure, run_ladder
from .baselines.milp import solve_window
from .config import LEGACY_RUN, RunConfig, TariffConfig
from .data.loaders import (attach_prices, fetch_spot_prices, load_legacy_csv,
                           make_dimming_series, to_resolution)
from .market import settlement, tariff
from .model import site


def _prepare(path: str, run: RunConfig, price_csv: str | None = None) -> pd.DataFrame:
    df = load_legacy_csv(path)
    if price_csv:
        df = attach_prices(df, pd.read_parquet(price_csv))
    df = to_resolution(df, run.dt)
    return tariff.build_prices(df, run.tariff)


def _arrays(df: pd.DataFrame):
    return (df["load_kw"].to_numpy(), df["pv_kw"].to_numpy(),
            df["price_import"].to_numpy(), df["price_export"].to_numpy())


def _report(results: dict[str, dict], run: RunConfig, title: str) -> None:
    print(f"\n{title}")
    print("=" * len(title))
    hdr = f"{'controller':<24}{'net cost EUR':>14}{'import kWh':>12}{'export kWh':>12}" \
          f"{'cycles':>9}{'viol.':>7}"
    print(hdr)
    print("-" * len(hdr))
    for name, r in results.items():
        c = r["cost"]
        viol = sum(r["violations"].values())
        cycles = float(np.sum(r["throughput"])) / (2 * run.site.usable_kwh)
        print(f"{name:<24}{c['net_cost']:>14.3f}"
              f"{float(np.sum(r['p_imp'])) * run.dt:>12.2f}"
              f"{float(np.sum(r['p_exp'])) * run.dt:>12.2f}"
              f"{cycles:>9.2f}{viol:>7d}")

    if "B2 perfect foresight" in results and "B3 rolling MPC" in results:
        b2 = results["B2 perfect foresight"]["cost"]["net_cost"]
        b3 = results["B3 rolling MPC"]["cost"]["net_cost"]
        b1 = results["B1 rule-based"]["cost"]["net_cost"]
        print(f"\nheadroom  B1 - B2 = {b1 - b2:8.3f} EUR   (what any smart control chases)")
        print(f"          B3 - B2 = {b3 - b2:8.3f} EUR   (what RL still has to play for)")
        print(f"B3 recovers {(b1 - b3) / (b1 - b2) * 100:5.1f}% of the B1->B2 headroom")
        if "solve_time_s" in results["B3 rolling MPC"]:
            st = results["B3 rolling MPC"]["solve_time_s"]
            print(f"B3 solve time: mean {st.mean() * 1000:.0f} ms, max {st.max() * 1000:.0f} ms "
                  f"per decision ({len(st)} solves)")


# ------------------------------------------------------------------------------ commands
def cmd_legacy(args) -> None:
    """Reproduce the original smart_weekly.py: hourly, day-by-day, single price."""
    run = LEGACY_RUN
    df = _prepare(args.data, run)
    load, pv, pi, pe = _arrays(df)

    soc = run.site.soc_init
    parts = []
    for d0 in range(0, len(df), 24):
        sl = slice(d0, min(d0 + 24, len(df)))
        sol = solve_window(load[sl], pv[sl], pi[sl], pe[sl], run.dt, run.site, soc,
                           use_binaries=True,
                           throughput_cap=run.site.e_throughput_max_per_day,
                           steps_per_day=24)
        if sol is None:
            print(f"day starting {d0}: INFEASIBLE", file=sys.stderr)
            return
        parts.append(sol)
        soc = float(sol["soc"][-1])

    res = {k: np.concatenate([p[k] for p in parts])
           for k in ("soc", "p_bat", "p_ch", "p_dis", "p_imp", "p_exp", "throughput")}
    res["cost"] = settlement.settle(res, pi, pe, run.dt, run.site)
    res["violations"] = site.check_feasible(res, run.dt, run.site)
    res["balance_err_kw"] = site.energy_balance_error(res, load, pv, run.dt, run.site)

    # The original maximised sum(EP*(P_dc - P_ch)); recompute it for direct comparison.
    legacy_obj = float(np.sum(df["spot_eur_per_kwh"].to_numpy() * res["p_bat"]))
    _report({"legacy MILP (hourly)": res}, run, "Legacy model reproduction")
    print(f"\noriginal objective  sum(EP*(P_dc-P_ch)) = {legacy_obj:.6f} EUR")
    print(f"energy-balance max error            = {res['balance_err_kw']:.2e} kW")
    print(f"final SoC                           = {res['soc'][-1]:.4f} kWh")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({k: v for k, v in res.items() if isinstance(v, np.ndarray)},
                     index=df.index).to_csv(args.out)
        print(f"written: {args.out}")


def cmd_ladder(args) -> None:
    run = RunConfig(dt=args.dt, horizon_steps=args.horizon,
                    tariff=TariffConfig(export_mode=args.export_mode,
                                        para_14a_module=args.module))
    df = _prepare(args.data, run, args.prices)
    load, pv, pi, pe = _arrays(df)
    dim = make_dimming_series(df.index, run.para_14a)

    print(f"steps: {len(df)}  dt: {run.dt} h  horizon: {run.horizon_steps} steps "
          f"({run.horizon_steps * run.dt:.0f} h)")
    print(tariff.describe(run.tariff, float(df['spot_eur_per_kwh'].mean())))

    res = run_ladder(load, pv, pi, pe, run, dim_limit=dim, progress=args.progress)
    _report(res, run, f"Benchmark ladder at dt={run.dt} h")


def cmd_resolution(args) -> None:
    """RQ1: how much does hourly modelling bias the result versus 15 minutes?"""
    rows = []
    for dt, horizon in ((1.0, 24), (0.25, 96)):
        run = RunConfig(dt=dt, horizon_steps=horizon,
                        tariff=TariffConfig(export_mode=args.export_mode))
        df = _prepare(args.data, run, args.prices)
        load, pv, pi, pe = _arrays(df)
        res = run_ladder(load, pv, pi, pe, run, include_b3=not args.fast)
        for name, r in res.items():
            rows.append({
                "dt_h": dt, "controller": name,
                "net_cost_EUR": r["cost"]["net_cost"],
                "peak_import_kW": float(np.max(r["p_imp"])),
                "peak_export_kW": float(np.max(r["p_exp"])),
                "cycles": float(np.sum(r["throughput"])) / (2 * run.site.usable_kwh),
            })
    out = pd.DataFrame(rows)
    print("\nResolution study (same week, same tariff, same method)")
    print(out.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    piv = out.pivot(index="controller", columns="dt_h", values="net_cost_EUR")
    if 1.0 in piv.columns and 0.25 in piv.columns:
        piv["bias_EUR"] = piv[1.0] - piv[0.25]
        piv["bias_pct"] = piv["bias_EUR"] / piv[0.25].abs() * 100
        print("\nHourly minus 15-min (positive = hourly OVERSTATES cost):")
        print(piv.to_string(float_format=lambda x: f"{x:,.3f}"))


def cmd_modules(args) -> None:
    """RQ4: which s14a EnWG network-charge module is best for this site?"""
    rows = []
    for module in ("none", "module_1", "module_2", "module_3"):
        run = RunConfig(dt=args.dt, horizon_steps=args.horizon,
                        tariff=TariffConfig(export_mode=args.export_mode,
                                            para_14a_module=module))
        df = _prepare(args.data, run, args.prices)
        load, pv, pi, pe = _arrays(df)
        res = run_ladder(load, pv, pi, pe, run, include_b3=not args.fast)
        for name, r in res.items():
            cost = r["cost"]["net_cost"]
            # Module 1 is a fixed annual credit: no marginal effect, so it is applied here
            # rather than inside the tariff, pro-rated over the evaluated period.
            if module == "module_1":
                years = len(df) * run.dt / 8760.0
                cost -= run.tariff.module_1_annual_reduction * years
            rows.append({"module": module, "controller": name, "net_cost_EUR": cost})
    out = pd.DataFrame(rows).pivot(index="controller", columns="module", values="net_cost_EUR")
    print("\ns14a EnWG module comparison (net cost EUR, lower is better)")
    print(out.to_string(float_format=lambda x: f"{x:,.3f}"))


def cmd_fetch(args) -> None:
    df = fetch_spot_prices(args.start, args.end, args.cache)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out)
    print(f"{len(df)} price points {df.index[0]} .. {df.index[-1]}")
    print(f"mean {df['spot_eur_per_kwh'].mean():.4f} EUR/kWh | "
          f"negative in {(df['spot_eur_per_kwh'] < 0).mean() * 100:.1f}% of steps")
    print(f"written: {args.out}")


def cmd_build_data(args) -> None:
    from .data.dataset import build_site_dataset, save_dataset
    df = build_site_dataset(args.start, args.end, raw_dir=args.cache)
    path = save_dataset(df, args.out)
    years = df.groupby(df.index.tz_convert("Europe/Berlin").year)
    print(f"{len(df)} quarter-hours {df.index[0]} .. {df.index[-1]}")
    print((years[["load_kw", "pv_kw"]].sum() * 0.25).round(0).to_string())
    print(f"written: {path}")


def cmd_rolling_eval(args) -> None:
    from .data.dataset import load_dataset
    from .experiments.rolling_eval import run_rolling_eval
    from .experiments.rolling_eval import DEFAULT_VARIANTS, MAIN_VARIANTS
    variants = MAIN_VARIANTS if args.variants == "main" else DEFAULT_VARIANTS
    res = run_rolling_eval(load_dataset(args.data), variants=variants, out_dir=args.out)
    with pd.option_context("display.width", 140, "display.max_columns", 20):
        print(res["summary"].round(2).to_string())
        print()
        print(res["capture"].round(1).to_string())


def cmd_household_sweep(args) -> None:
    from .experiments.household_sweep import run_household_sweep
    screen = pd.read_csv(args.screen, index_col=0)
    households = [h for h in screen.index if not screen.loc[h, "pv_suspect"]]
    res = run_household_sweep(households, args.data, args.htw, args.out, args.workers)
    print(res["capture_b3_pct"].describe().round(1).to_string())


def cmd_quarter_hour_study(args) -> None:
    from .data.dataset import load_dataset
    from .experiments.resolution_study import run_resolution_study
    res = run_resolution_study(load_dataset(args.data), out_dir=args.out)
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(res["summary"].round(2).to_string())


def cmd_rl_eval(args) -> None:
    from .experiments.rl_eval import run_rl_eval
    res = run_rl_eval(args.data, steps=args.steps, seeds=args.seeds, workers=args.workers,
                      b3_monthly_csv=args.b3, out_dir=args.out,
                      reward_mode=args.reward_mode, action_mode=args.action_mode,
                      model_dir=args.model_dir)
    print(res["summary"].round(2).to_string())
    print(res["seeds"].round(3).to_string())
def cmd_sizing(args) -> None:
    from .experiments.sizing import run_sizing_grid
    res = run_sizing_grid(args.data, controllers=tuple(args.controllers.split(",")),
                          workers=args.workers, out_dir=args.out)
    print("\nBattery & Inverter Sizing Grid:")
    with pd.option_context("display.width", 140, "display.max_columns", 15):
        print(res.to_string(index=False))


def cmd_sensitivity(args) -> None:
    from .experiments.sensitivity import run_type1_sensitivity, run_type2_fixed_pv_sensitivity

    if args.type in ("1", "all"):
        print("\n=== Type 1 Component-Level Sensitivity Analysis (1,016 Feasible Configurations) ===")
        df = run_type1_sensitivity()
        print(f"Total feasible configurations evaluated: {len(df)}")
        print("\nTop 10 Configurations by Return on Capital Expenditure (ROCE):")
        cols = ["rank", "config_id", "pv_kwp", "bess_kwh", "inverter_kw", "capex_eur", "opt_profit_eur", "roce"]
        print(df[cols].head(10).to_string(index=False))

    if args.type in ("2", "all"):
        fixed_pvs = [args.fixed_pv] if args.fixed_pv else [5.0, 10.0, 15.0]
        for pv in fixed_pvs:
            print(f"\n=== Type 2 Targeted Sensitivity for Fixed PV = {pv} kWp ===")
            res = run_type2_fixed_pv_sensitivity(pv)
            opt = res["optimum"]
            print(f"Feasible (BESS, Inverter) pairs: {res['valid_count']}")
            print(f"Optimal Sweet Spot: {opt['bess_kwh']} kWh BESS with {opt['inverter_kw']} kW Inverter "
                  f"(EPR={opt['epr']}, PBR={opt['pbr']}, CAPEX={opt['capex_eur']} EUR, ROCE={opt['roce']:.4f})")
            if args.verbose:
                print("\nROCE Heatmap Matrix (Rows = BESS kWh, Cols = Inverter kW):")
                print(res["heatmap"].to_string())


def cmd_national_impact(args) -> None:
    from .experiments.national_impact import (
        REPRESENTATIVE_DAYS,
        evaluate_fossil_gas_displacement,
        get_national_totals,
    )

    print(f"\n=== Germany-Wide System-Level Impact Analysis ({args.year}) ===")
    totals = get_national_totals(args.year)
    print(f"Total Residential BESS Fleet: {totals['total_bess_units']:,} systems")
    print(f"Aggregated Storage Capacity:  {totals['total_capacity_gwh']:.2f} GWh ({totals['total_capacity_mwh']:,.1f} MWh)")
    print(f"Aggregated Inverter Power:    {totals['total_power_gw']:.2f} GW ({totals['total_power_mw']:,.1f} MW)")
    print(f"Average System Sizing:        {totals['mean_capacity_kwh']:.2f} kWh / {totals['mean_power_kw']:.2f} kW")

    if args.day:
        day_key = args.day
        if day_key in REPRESENTATIVE_DAYS:
            d = REPRESENTATIVE_DAYS[day_key]
            disp = evaluate_fossil_gas_displacement(d["peak_shift_mwh"], d["fossil_gas_mwh"])
            print(f"\nRepresentative Day: {day_key.upper()} ({d['date']}) - {d['description']}")
            print(f"Peak Operational Hour:         {d['peak_hour']:02d}:00")
            print(f"Nationwide Grid Shift:         {d['peak_shift_mwh']:+,.2f} MWh")
            print(f"Marginal Fossil Gas Displaced:  {disp['displaced_gas_mwh']:,.1f} MWh ({disp['displacement_pct']:.1f}%)")
            print(f"System Cost Savings (Gas vs BESS): {disp['cost_saving_eur']:,.2f} EUR/h")


def cmd_aggregate_load(args) -> None:
    from .experiments.aggregate_load_fit import run_fit
    from .experiments.aggregate_load_states import run_states

    if not args.states_only:
        m = run_fit(args.htw, args.out, n_splits=args.splits, n_boot=args.boot)
        print(f"\nAggregate load model: beta {m.beta:+.4f} kWh/K per household-day, "
              f"normal-weather energy {m.a_ref:,.1f} kWh/a, proxy site {m.proxy}")
    run_states(args.out)
    summary = pd.read_csv(f"{args.out}/validation_summary.csv")
    with pd.option_context("display.width", 160, "display.max_colwidth", 50):
        print(summary.to_string(index=False))
    print(f"\n{int(summary['passed'].sum())} of {len(summary)} checks passed")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="prosumer", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--data", required=True, help="legacy CSV path")
        sp.add_argument("--prices", default=None, help="parquet of spot prices to substitute")
        sp.add_argument("--export-mode", default="spot_gross_floored",
                        choices=["spot_gross_floored", "spot", "market_premium", "feed_in_tariff"])
        sp.add_argument("--fast", action="store_true", help="skip B3 (much faster)")

    sp = sub.add_parser("legacy"); sp.add_argument("--data", required=True)
    sp.add_argument("--out", default=None); sp.set_defaults(func=cmd_legacy)

    sp = sub.add_parser("ladder"); common(sp)
    sp.add_argument("--dt", type=float, default=0.25)
    sp.add_argument("--horizon", type=int, default=96)
    sp.add_argument("--module", default="none",
                    choices=["none", "module_1", "module_2", "module_3"])
    sp.add_argument("--progress", action="store_true"); sp.set_defaults(func=cmd_ladder)

    sp = sub.add_parser("resolution"); common(sp); sp.set_defaults(func=cmd_resolution)

    sp = sub.add_parser("modules"); common(sp)
    sp.add_argument("--dt", type=float, default=0.25)
    sp.add_argument("--horizon", type=int, default=96); sp.set_defaults(func=cmd_modules)

    sp = sub.add_parser("fetch")
    sp.add_argument("--start", required=True); sp.add_argument("--end", required=True)
    sp.add_argument("--cache", default="data/raw")
    sp.add_argument("--out", default="data/processed/spot_de_lu.parquet")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("build-data", help="15-min load, PV, PV forecast and prices")
    sp.add_argument("--start", default="2024-01-01"); sp.add_argument("--end", default="2026-09-18")
    sp.add_argument("--cache", default="data/raw")
    sp.add_argument("--out", default="data/processed/site_2024_2026.parquet")
    sp.set_defaults(func=cmd_build_data)

    sp = sub.add_parser("rolling-eval", help="B1/B2/B3 on 2025-2026, trained on 2024")
    sp.add_argument("--data", default="data/processed/site_2024_2026.parquet")
    sp.add_argument("--out", default="reports/rolling_eval")
    sp.add_argument("--variants", default="all", choices=["all", "main"])
    sp.set_defaults(func=cmd_rolling_eval)

    sp = sub.add_parser("household-sweep", help="rolling evaluation for all HTW households")
    sp.add_argument("--data", default="data/processed/site_2024_2026.parquet")
    sp.add_argument("--htw", default="data/processed/htw_74_15min_2010.parquet")
    sp.add_argument("--screen", default="reports/htw_pv_screen.csv")
    sp.add_argument("--out", default="reports/household_sweep.csv")
    sp.add_argument("--workers", type=int, default=4)
    sp.set_defaults(func=cmd_household_sweep)

    sp = sub.add_parser("quarter-hour-study",
                        help="value of 15-min day-ahead prices after 2025-10-01")
    sp.add_argument("--data", default="data/processed/site_2024_2026_htw_H28.parquet")
    sp.add_argument("--out", default="reports/quarter_hour_study_H28")
    sp.set_defaults(func=cmd_quarter_hour_study)

    sp = sub.add_parser("rl-eval", help="train SAC on 2024, test on 2025-2026 against B3")
    sp.add_argument("--data", default="data/processed/site_2024_2026_htw_H28.parquet")
    sp.add_argument("--b3", default="reports/rolling_eval_htw_H28/rolling_eval_monthly_cost.csv")
    sp.add_argument("--out", default="reports/rl_eval_H28")
    sp.add_argument("--steps", type=int, default=500_000)
    sp.add_argument("--seeds", type=int, default=3)
    sp.add_argument("--workers", type=int, default=3)
    sp.add_argument("--reward-mode", default="differential", choices=["net_cost", "differential"])
    sp.add_argument("--action-mode", default="rescale", choices=["clip", "rescale"])
    sp.add_argument("--model-dir", default="data/processed/rl_models")
    sp.set_defaults(func=cmd_rl_eval)

    sp = sub.add_parser("sizing", help="evaluate battery and inverter sizing grid")
    sp.add_argument("--data", default="data/processed/site_2024_2026_htw_H28.parquet")
    sp.add_argument("--out", default="reports/sizing_grid")
    sp.add_argument("--controllers", default="b3,b2")
    sp.add_argument("--workers", type=int, default=6)
    sp.set_defaults(func=cmd_sizing)

    sp = sub.add_parser("sensitivity", help="thesis sensitivity analysis (type 1 3D grid and type 2 fixed-PV)")
    sp.add_argument("--type", default="all", choices=["1", "2", "all"])
    sp.add_argument("--fixed-pv", type=float, default=None, choices=[5.0, 10.0, 15.0])
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_sensitivity)

    sp = sub.add_parser("national-impact", help="Germany-wide 16-state impact and merit-order displacement")
    sp.add_argument("--year", type=int, default=2024, choices=[2024, 2025, 2026])
    sp.add_argument("--day", default="summer",
                    choices=["winter", "transition", "summer", "negative_price", "scarcity_peak"])
    sp.set_defaults(func=cmd_national_impact)

    sp = sub.add_parser("aggregate-load",
                        help="fit the per-state aggregate household load to HTW and validate it")
    sp.add_argument("--htw", default="data/processed/htw_74_15min_2010.parquet")
    sp.add_argument("--out", default="reports/aggregate_load")
    sp.add_argument("--splits", type=int, default=50)
    sp.add_argument("--boot", type=int, default=1000)
    sp.add_argument("--states-only", action="store_true",
                    help="skip the refit and rebuild only the per-state summary")
    sp.set_defaults(func=cmd_aggregate_load)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

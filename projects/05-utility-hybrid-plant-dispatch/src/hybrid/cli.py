"""Command-line entry point for the hybrid-plant project.

    python -m hybrid.cli ladder    --year 2024 --days 30
    python -m hybrid.cli sizing    --year 2024 --days 30   connection-ratio sweep (RQ2)
    python -m hybrid.cli negprice  --year 2024 --days 30   negative-price rule by vintage (RQ4)
    python -m hybrid.cli decompose --year 2024 --days 30   where the battery's value comes from
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .baselines import run_ladder
from .config import MarketConfig, PlantConfig, RunConfig
from .data import describe, load_year, to_plant
from .market import rebap_series


def _setup(args, plant: PlantConfig | None = None, market: MarketConfig | None = None):
    run = RunConfig(dt=0.25, horizon_steps=args.horizon,
                    plant=plant or PlantConfig(),
                    market=market or MarketConfig(negative_price_rule=args.neg_rule))
    df = load_year(args.year, args.days, run.dt)
    wind, pv, price = to_plant(df, run.plant.wind_mw, run.plant.pv_mw)
    rng = np.random.default_rng(run.seed)
    rebap = rebap_series(len(price), price, run.market, rng)
    # premium rate: applicable value minus the market value realised over this window
    premium = max(0.0, run.market.anzulegender_wert_wind - float(np.mean(price)))
    return run, wind, pv, price, rebap, premium


def _report(res: dict, title: str) -> None:
    print(f"\n{title}")
    print("=" * len(title))
    hdr = (f"{'controller':<26}{'net EUR':>12}{'export MWh':>12}"
           f"{'curt chosen':>12}{'curt forced':>12}{'viol.':>7}")
    print(hdr)
    print("-" * len(hdr))
    for name, r in res.items():
        c = r["cost"]
        print(f"{name:<26}{c['net_revenue']:>12,.0f}{c['export_mwh']:>12,.0f}"
              f"{c['chosen_curtail_mwh']:>12,.0f}{c['forced_curtail_mwh']:>12,.0f}"
              f"{sum(r['violations'].values()):>7d}")
    if "B0 no battery" in res:
        b0 = res["B0 no battery"]["cost"]["net_revenue"]
        for k in ("B1 curtailment avoidance", "B3 rolling MPC", "B2 perfect foresight"):
            if k in res:
                d = res[k]["cost"]["net_revenue"] - b0
                print(f"  {k:<28} vs B0: {d:+,.0f} EUR ({d / abs(b0) * 100:+.2f}%)")


def cmd_ladder(args) -> None:
    run, wind, pv, price, rebap, prem = _setup(args)
    print(f"{len(price)} steps | {args.days} days of {args.year} | "
          f"plant {run.plant.wind_mw:.0f} MW wind + {run.plant.pv_mw:.0f} MW PV + "
          f"{run.plant.bess_mw:.0f} MW/{run.plant.bess_mwh:.0f} MWh behind "
          f"{run.plant.conn_mw:.0f} MW (overbuild {run.plant.overbuild_ratio:.2f}x)")
    print(describe(wind, pv, price, run.plant.conn_mw, run.dt))
    print(f"market premium rate: {prem:.2f} EUR/MWh | "
          f"negative-price rule: {run.market.negative_price_rule}")
    res = run_ladder(wind, pv, price, run, prem, rebap, include_b3=not args.fast,
                     progress=args.progress)
    _report(res, f"Hybrid plant ladder, {args.days} days of {args.year}")
    if "B3 rolling MPC" in res and "solve_time_s" in res["B3 rolling MPC"]:
        st = res["B3 rolling MPC"]["solve_time_s"]
        print(f"\nB3 solve time: mean {st.mean() * 1000:.0f} ms, max {st.max() * 1000:.0f} ms")


def cmd_sizing(args) -> None:
    """RQ2: what is the optimal degree of connection undersizing?"""
    rows = []
    # The sweep spans connections BELOW the observed generation peak, because national
    # capacity factors are far smoother than any single site: aggregated across Germany the
    # combined wind+PV factor rarely exceeds ~0.45, so a connection sized at 75 % of
    # nameplate never binds and the curtailment problem disappears entirely. Sweeping down to
    # 25 % of nameplate is what makes the trade-off visible in this dataset. On site-level
    # data the same curve would sit much further right - which is precisely why site data is
    # this project's top acquisition priority.
    for conn in (20.0, 25.0, 30.0, 40.0, 60.0):
        plant = PlantConfig(conn_mw=conn)
        run, wind, pv, price, rebap, prem = _setup(args, plant=plant)
        res = run_ladder(wind, pv, price, run, prem, rebap, include_b3=False)
        for name in ("B0 no battery", "B2 perfect foresight"):
            c = res[name]["cost"]
            rows.append({"conn_MW": conn, "overbuild": plant.overbuild_ratio,
                         "controller": name, "net_EUR": c["net_revenue"],
                         "forced_curt_MWh": c["forced_curtail_mwh"]})
    df = pd.DataFrame(rows)
    print("\nConnection-capacity sweep (80 MW installed generation)")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    piv = df.pivot(index="conn_MW", columns="controller", values="net_EUR")
    piv["battery_value_EUR"] = piv["B2 perfect foresight"] - piv["B0 no battery"]
    print("\nValue of the battery by connection size:")
    print(piv.to_string(float_format=lambda x: f"{x:,.0f}"))


def cmd_negprice(args) -> None:
    """RQ4: what does the tightened negative-price rule cost, by plant vintage?"""
    rows = []
    # Two vintages. The applicable value (anzulegender Wert) is set at commissioning, so an
    # older or offshore plant carries a higher one and IS in the money against 2024 prices;
    # a recent onshore plant at ~73.5 EUR/MWh is not, and for it the negative-price rule is
    # economically irrelevant because there is no premium to suspend. That contrast is the
    # point of the table.
    for aw, label in ((73.5, "AW 73.5 (recent onshore)"), (95.0, "AW 95.0 (older vintage)")):
        for rule in ("none", "six_hour", "new_2025"):
            mk = MarketConfig(negative_price_rule=rule,
                              anzulegender_wert_wind=aw, anzulegender_wert_pv=aw - 5)
            run, wind, pv, price, rebap, _ = _setup(args, market=mk)
            prem = max(0.0, aw - float(np.mean(price)))
            res = run_ladder(wind, pv, price, run, prem, rebap, include_b3=False)
            for name, r in res.items():
                rows.append({"vintage": label, "rule": rule, "controller": name,
                             "premium_rate": prem,
                             "net_EUR": r["cost"]["net_revenue"],
                             "chosen_curt_MWh": r["cost"]["chosen_curtail_mwh"]})
    df = pd.DataFrame(rows)
    print("\nNegative-price rule (EEG s51 / Solarspitzengesetz) by plant vintage")
    for label, g in df.groupby("vintage"):
        prem = g["premium_rate"].iloc[0]
        print(f"\n{label} - premium rate {prem:.2f} EUR/MWh")
        print(g.pivot(index="controller", columns="rule", values="net_EUR")
              .to_string(float_format=lambda x: f"{x:,.0f}"))
        print("  self-chosen curtailment (MWh), should RISE as the rule tightens:")
        print(g.pivot(index="controller", columns="rule", values="chosen_curt_MWh")
              .to_string(float_format=lambda x: f"{x:,.1f}"))
    print("\nNote: with a premium rate of zero the rule cannot bite - there is no premium to "
          "suspend. In 2024 the DE-LU market value exceeded the applicable value for a recent "
          "onshore plant, so the tightened rule was economically irrelevant for that vintage.")


def cmd_decompose(args) -> None:
    """RQ1: is the battery's value curtailment avoidance, or arbitrage?"""
    run, wind, pv, price, rebap, prem = _setup(args)
    base = run_ladder(wind, pv, price, run, prem, rebap, include_b3=False)
    v_full = (base["B2 perfect foresight"]["cost"]["net_revenue"]
              - base["B0 no battery"]["cost"]["net_revenue"])

    # remove curtailment risk by making the connection unconstrained
    big = PlantConfig(conn_mw=200.0)
    run2, w2, p2, pr2, rb2, prem2 = _setup(args, plant=big)
    nocurt = run_ladder(w2, p2, pr2, run2, prem2, rb2, include_b3=False)
    v_arbitrage = (nocurt["B2 perfect foresight"]["cost"]["net_revenue"]
                   - nocurt["B0 no battery"]["cost"]["net_revenue"])

    print("\nWhere the battery's value comes from")
    print(f"  total value (as built)          {v_full:>12,.0f} EUR")
    print(f"  value with an unconstrained POI {v_arbitrage:>12,.0f} EUR   <- pure arbitrage")
    print(f"  attributable to curtailment     {v_full - v_arbitrage:>12,.0f} EUR")
    if abs(v_full) > 1e-9:
        print(f"  curtailment share: {(v_full - v_arbitrage) / v_full * 100:5.1f}%")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hybrid", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("ladder", cmd_ladder), ("sizing", cmd_sizing),
                     ("negprice", cmd_negprice), ("decompose", cmd_decompose)):
        sp = sub.add_parser(name)
        sp.add_argument("--year", type=int, default=2024)
        sp.add_argument("--days", type=int, default=30)
        sp.add_argument("--horizon", type=int, default=96)
        sp.add_argument("--neg-rule", default="new_2025",
                        choices=["none", "six_hour", "new_2025"])
        sp.add_argument("--fast", action="store_true")
        sp.add_argument("--progress", action="store_true")
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

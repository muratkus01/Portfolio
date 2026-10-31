"""Command-line entry point for the hybrid-plant project.

    python -m hybrid.cli ladder    --year 2025 --days 30
    python -m hybrid.cli sizing    --year 2025 --days 30   connection-ratio sweep (RQ2)
    python -m hybrid.cli negprice  --year 2025 --days 30   negative-price rule by vintage (RQ4)
    python -m hybrid.cli decompose --year 2025 --days 30   where the battery's value comes from
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .baselines import run_ladder
from .config import NEGATIVE_PRICE_RULES, MarketConfig, PlantConfig, RunConfig
from .data import describe, load_year, to_plant
from .market import (market_values, planning_rates, premium_rates, premium_table,
                     rebap_series, vintage_rules)


# The default plant, at MarketConfig's applicable values from the 2024/2025 tender ceilings, is
# commissioned and awarded from 2023 on, so it is paid against its technology's ANNUAL market
# value (EEG 2023 Anlage 1 Nr. 2), as negprice's recent vintage is. --neg-rule varies the
# negative-price rule as a sensitivity on this plant; its applicable values and their annual
# reference stay.
_DEFAULT_PERIOD = "year"


def _setup(args, plant: PlantConfig | None = None, market: MarketConfig | None = None):
    """Run settings and the evaluated window.

    args: parsed CLI arguments, e.g. year=2024, days=20, horizon=96, neg_rule="new_2025".
    plant, market: settings to run instead of the defaults.
    Returns (run, index, wind, pv, price, rebap): the RunConfig, the window's UTC timestamps,
    and per step the plant's available wind and PV (MW), the day-ahead price and a synthetic
    reBAP (EUR/MWh).
    """
    run = RunConfig(dt=0.25, horizon_steps=args.horizon,
                    plant=plant or PlantConfig(),
                    market=market or MarketConfig(negative_price_rule=args.neg_rule))
    df = load_year(args.year, args.days, run.dt)
    wind, pv, price = to_plant(df, run.plant.wind_mw, run.plant.pv_mw)
    rng = np.random.default_rng(run.seed)
    rebap = rebap_series(len(price), price, run.market, rng)
    return run, df.index, wind, pv, price, rebap


def _national_weights(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-technology weights of the market-value proxy, from `load_year`'s factors.

    National feed-in over installed capacity: the capacity is one figure within a month, so
    the factor weights the price as the feed-in does. MW Wind an Land weights by onshore
    feed-in alone (Anlage 1 Nr. 3.3.2), which `wind_cf` is. PV weights by `pv_feed_in_cf`,
    public feed-in, not by the plant factor `pv_cf`, which also counts self-consumption.
    """
    return frame[["wind_cf", "pv_feed_in_cf"]].set_axis(["wind", "pv"], axis=1)


def _realised_premium(year: pd.DataFrame, index: pd.DatetimeIndex, wind: np.ndarray,
                      pv: np.ndarray, applicable: dict, period: str) -> tuple:
    """Premium table and per-step rate a plant is settled on: the REALISED market values.

    The premium is paid against the market value of the whole calendar month or year (EEG
    2023 Anlage 1 Nr. 3.1.1, 4.1.1), so the values come from the full year's data, not from
    the window alone. Each technology gets max(0, AW - market value), and the plant's rate
    weights the two by its own generation (`market.premium_table`).

    year: `load_year(year)`, the whole year. index: the window's timestamps.
    wind, pv: the plant's available generation on the window, MW per step.
    applicable: anzulegender Wert per technology, EUR/MWh, e.g. {"wind": 73.5, "pv": 68.0}.
    period: "month" or "year".
    Returns (premium_table frame, the plant's rate per step as np.ndarray).
    """
    values = market_values(year["price_eur_mwh"], _national_weights(year), period)
    table = premium_table(index, {"wind": wind, "pv": pv}, values, applicable, period)
    return table, premium_rates(index, table, period)


def _applicable(market: MarketConfig) -> dict:
    """The plant's applicable values per technology, EUR/MWh."""
    return {"wind": market.anzulegender_wert_wind, "pv": market.anzulegender_wert_pv}


def _premium(args, run: RunConfig, index: pd.DatetimeIndex, wind: np.ndarray,
             pv: np.ndarray) -> tuple:
    """The default plant's premium on the window: table and per-step rate it is settled on.

    B2 plans on the same rate, as perfect foresight may; B0 and B1 read no prices.
    Returns (premium_table frame, rate per step in EUR/MWh).
    """
    year = load_year(args.year, None, run.dt)
    return _realised_premium(year, index, wind, pv, _applicable(run.market), _DEFAULT_PERIOD)


def _b3_planning_premium(args, run: RunConfig, index: pd.DatetimeIndex) -> np.ndarray:
    """B3's premium per step: the plant's realised monthly rate of the month before.

    A market value is known only once its month is over (`market.planning_rates`), so B3, a
    deployable controller, plans each month on the previous month's realised rate. For a
    plant paid against the annual value that is its latest realised value too, as the year's
    own is known only after December. A window starts on 1 January, so its first month needs
    December of the previous year, which this loads.

    args: parsed CLI arguments, e.g. year=2024. run: the run's settings.
    index: the window's timestamps.
    Returns B3's rate per step, EUR/MWh.
    """
    frame = pd.concat([load_year(args.year - 1, None, run.dt),
                       load_year(args.year, None, run.dt)])
    wind, pv, _ = to_plant(frame, run.plant.wind_mw, run.plant.pv_mw)
    values = market_values(frame["price_eur_mwh"], _national_weights(frame), "month")
    monthly = premium_table(frame.index, {"wind": wind, "pv": pv}, values,
                            _applicable(run.market), "month")
    return planning_rates(index, monthly)


def _print_premium(table: pd.DataFrame, period: str) -> None:
    """Print a premium table's premiums per technology and the plant's rate, by period."""
    print(f"market premium, settled on the realised {'annual' if period == 'year' else 'monthly'}"
          " market value (a proxy, see negprice), EUR/MWh:")
    for key, t in table.iterrows():
        print(f"  {key}: wind {t['premium_wind']:.2f}, PV {t['premium_pv']:.2f}, "
              f"plant {t['rate']:.2f}")


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
    run, index, wind, pv, price, rebap = _setup(args)
    table, prem = _premium(args, run, index, wind, pv)
    print(f"{len(price)} steps | {args.days} days of {args.year} | "
          f"plant {run.plant.wind_mw:.0f} MW wind + {run.plant.pv_mw:.0f} MW PV + "
          f"{run.plant.bess_mw:.0f} MW/{run.plant.bess_mwh:.0f} MWh behind "
          f"{run.plant.conn_mw:.0f} MW (overbuild {run.plant.overbuild_ratio:.2f}x)")
    print(describe(wind, pv, price, run.plant.conn_mw, run.dt))
    print(f"negative-price rule: {run.market.negative_price_rule}")
    _print_premium(table, _DEFAULT_PERIOD)
    plan = None
    if not args.fast:
        plan = _b3_planning_premium(args, run, index)
        by_month = pd.Series(plan, index=index.tz_convert("Europe/Berlin").strftime("%Y-%m"))
        print("B3 plans each month on the realised monthly rate of the month before, EUR/MWh: "
              + ", ".join(f"{m} {r:.2f}" for m, r in by_month.groupby(level=0).first().items()))
    res = run_ladder(wind, pv, price, run, prem, rebap, include_b3=not args.fast,
                     progress=args.progress, b3_premium_rate=plan)
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
        run, index, wind, pv, price, rebap = _setup(args, plant=plant)
        _, prem = _premium(args, run, index, wind, pv)
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


# Two vintages, and the market value each one is paid against. The applicable value
# (anzulegender Wert) is set by the plant's tender award, or by law at commissioning, so an
# older or offshore plant carries a higher one than a recent onshore plant at ~73.5 EUR/MWh.
# The dates also fix the reference: a plant commissioned before 2023, or awarded before 2023,
# is paid against its technology's MONTHLY market value, any other plant against the ANNUAL
# value (EEG 2023 Anlage 1 Nr. 2).
# Where a plant gets no premium the negative-price rule is economically irrelevant, because
# there is no premium to suspend. That contrast is the point of the table; the note under it
# says which vintage got none in the evaluated window.
# Each entry: wind and PV applicable value (EUR/MWh), label, market-value period. The recent
# plant is the default plant of every other command, at the values MarketConfig sets and
# cites. The older vintage's values are a sensitivity, not a sourced award; both are printed.
_NEGPRICE_VINTAGES = ((73.5, 68.0, "AW 73.5 (recent onshore)", _DEFAULT_PERIOD),
                      (95.0, 90.0, "AW 95.0 (older vintage)", "month"))


def _vintage_market(aw_wind: float, aw_pv: float, rule: str) -> MarketConfig:
    """Market settings of a negprice vintage: its applicable values under rule `rule`."""
    return MarketConfig(negative_price_rule=rule,
                        anzulegender_wert_wind=aw_wind, anzulegender_wert_pv=aw_pv)


def _negprice_premiums(args, dt: float, index: pd.DatetimeIndex, wind: np.ndarray,
                       pv: np.ndarray) -> dict:
    """Premium table and per-step plant rate of each negprice vintage on the window.

    Every rung is settled on the realised value (`_realised_premium`), as the law settles. B0
    and B1 do not read prices; B2 (perfect foresight) may plan on the realised value. negprice
    runs no B3, which as a deployable controller could only plan on the previous month's.

    args: parsed CLI arguments, e.g. year=2024, days=20. dt: step length in hours.
    index: the window's timestamps. wind, pv: the plant's available generation on the
    window, MW per step.
    Returns {vintage label: (premium_table frame, plant rate per step as np.ndarray)}.
    """
    year = load_year(args.year, None, dt)
    return {label: _realised_premium(year, index, wind, pv, {"wind": aw_wind, "pv": aw_pv},
                                     period)
            for aw_wind, aw_pv, label, period in _NEGPRICE_VINTAGES}


def cmd_negprice(args) -> None:
    """RQ4: what does the tightened negative-price rule cost, by plant vintage?"""
    # One window for every scenario; only the market settings differ between them.
    base, index, wind, pv, price, rebap = _setup(args)
    premiums = _negprice_premiums(args, base.dt, index, wind, pv)
    # The rule is fixed by the vintage (commissioning or tender date), and one vintage's
    # threshold also moves with the evaluated year (market.vintage_rules). Columns run from the
    # oldest vintage's rule to the newest, which is from the loosest to the tightest.
    by_vintage = vintage_rules(args.year)
    rules = list(dict.fromkeys(by_vintage.values()))
    rows = []
    for aw_wind, aw_pv, label, _ in _NEGPRICE_VINTAGES:
        for rule in rules:
            run = base.with_(market=_vintage_market(aw_wind, aw_pv, rule))
            res = run_ladder(wind, pv, price, run, premiums[label][1], rebap, include_b3=False)
            for name, r in res.items():
                rows.append({"vintage": label, "rule": rule, "controller": name,
                             "net_EUR": r["cost"]["net_revenue"],
                             "chosen_curt_MWh": r["cost"]["chosen_curtail_mwh"]})
    _print_negprice(args.year, by_vintage, rules, premiums, pd.DataFrame(rows))


def _print_negprice(year: int, by_vintage: dict, rules: list, premiums: dict,
                    df: pd.DataFrame) -> None:
    """Print negprice's tables, and a note computed from the evaluated window."""
    print("\nNegative-price rule (EEG s51 / Solarspitzengesetz) by plant vintage")
    print(f"rule of each vintage in {year}, dated by commissioning or, for a tendered plant,\n"
          "  by the bid date of its tender where EEG s100 says so:")
    for vintage, rule in by_vintage.items():
        print(f"  {vintage:<22}{rule}")
    # Anlage 1 weights by the TSOs' online extrapolation (Nr. 5.1); the data layer reads
    # public generation instead, so these values only approximate the statutory ones.
    print("market value: a proxy, the day-ahead price weighted by public national generation\n"
          "  (EEG Anlage 1 Nr. 3.3, 4.3); the premium is settled on the TSOs' published values,\n"
          "  which this project does not load")
    no_premium = []
    for aw_wind, aw_pv, label, period in _NEGPRICE_VINTAGES:
        table = premiums[label][0]
        print(f"\n{label} - applicable value wind {aw_wind:.1f}, PV {aw_pv:.1f} EUR/MWh")
        print(f"{'annual' if period == 'year' else 'monthly'} market value and premium, "
              "EUR/MWh:")
        for key, t in table.iterrows():
            print(f"  {key} ({t['days']:.0f} days): wind {t['mw_wind']:.2f}, "
                  f"PV {t['mw_pv']:.2f} -> premium wind {t['premium_wind']:.2f}, "
                  f"PV {t['premium_pv']:.2f}, plant {t['rate']:.2f}")
        g = df[df["vintage"] == label]
        print(g.pivot(index="controller", columns="rule", values="net_EUR")[rules]
              .to_string(float_format=lambda x: f"{x:,.0f}"))
        print("  self-chosen curtailment (MWh), should RISE as the rule tightens:")
        print(g.pivot(index="controller", columns="rule", values="chosen_curt_MWh")[rules]
              .to_string(float_format=lambda x: f"{x:,.1f}"))
        zero = [f"{name} in {key}" for key, t in table.iterrows()
                for tech, name in (("wind", "wind"), ("pv", "PV"))
                if t[f"premium_{tech}"] <= 0]
        no_premium.append(f"{label} {', '.join(zero) or 'none'}")
    print(f"\nNote: a technology whose market value reaches its applicable value gets no "
          f"premium, and there the rule cannot bite - there is no premium to suspend. "
          f"No premium in this window ({year}): {'; '.join(no_premium)}.")


def cmd_decompose(args) -> None:
    """RQ1: is the battery's value curtailment avoidance, or arbitrage?"""
    run, index, wind, pv, price, rebap = _setup(args)
    _, prem = _premium(args, run, index, wind, pv)
    base = run_ladder(wind, pv, price, run, prem, rebap, include_b3=False)
    v_full = (base["B2 perfect foresight"]["cost"]["net_revenue"]
              - base["B0 no battery"]["cost"]["net_revenue"])

    # remove curtailment risk by making the connection unconstrained
    big = PlantConfig(conn_mw=200.0)
    run2, idx2, w2, p2, pr2, rb2 = _setup(args, plant=big)
    _, prem2 = _premium(args, run2, idx2, w2, p2)
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
        sp.add_argument("--year", type=int, default=2025)
        sp.add_argument("--days", type=int, default=30)
        sp.add_argument("--horizon", type=int, default=96)
        sp.add_argument("--neg-rule", default="new_2025", choices=NEGATIVE_PRICE_RULES)
        sp.add_argument("--fast", action="store_true")
        sp.add_argument("--progress", action="store_true")
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for the hybrid-plant project.

    python -m hybrid.cli ladder    --year 2025 --days 30
    python -m hybrid.cli sizing    --year 2025 --days 30   connection-ratio sweep (RQ2)
    python -m hybrid.cli negprice  --year 2025 --days 30   negative-price rule by vintage (RQ4)
    python -m hybrid.cli decompose --year 2025 --days 30   where the battery's value comes from
    python -m hybrid.cli b3-ablation --synthetic-demo      B3: forecast error x terminal value
    python -m hybrid.cli report                            the published tables, to reports/

A window runs --days days of 24 h from --start (MM-DD, Berlin time, 01-01 by default), or
the whole Berlin calendar year with --full-year. --out PATH also writes the result as a CSV,
one row per scenario and rung (`hybrid.tables`).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from . import synthetic, tables
from .baselines import ladder_tolerance, run_ladder
from .config import NEGATIVE_PRICE_RULES, MarketConfig, PlantConfig, RunConfig
from .data import describe, load_year, market_value_weights, to_plant
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
    df = load_window(args, run.dt)
    wind, pv, price = to_plant(df, run.plant.wind_mw, run.plant.pv_mw)
    rng = np.random.default_rng(run.seed)
    rebap = rebap_series(len(price), price, run.market, rng)
    return run, df.index, wind, pv, price, rebap


def load_window(args, dt: float) -> pd.DataFrame:
    """The evaluated window: `args.days` days of 24 h from `args.start` (MM-DD, Berlin time) in
    `args.year`, or the whole Berlin calendar year when `args.days` is None.

    args: parsed CLI arguments, e.g. year=2025, start="07-01", days=30.
    dt: step length in hours, e.g. 0.25.
    Returns the window's rows of `load_year(args.year)`.
    Raises ValueError when the window does not lie inside its calendar year: load_year loads
    one year, so a longer window would be cut short without a word.
    """
    begin = pd.Timestamp(f"{args.year}-{args.start}", tz="Europe/Berlin")
    if args.days is None:
        if args.start != "01-01":
            raise ValueError(f"--full-year covers {args.year} from 01-01, not from {args.start}")
        return _load(args, args.year, None, dt)
    end = begin + pd.Timedelta(days=args.days)
    if end > pd.Timestamp(f"{args.year + 1}-01-01", tz="Europe/Berlin"):
        raise ValueError(f"{args.days} days from {args.start} run past the end of {args.year}")
    if args.start == "01-01":
        # load_year cuts the year's first days itself, as every window was cut before --start
        return _load(args, args.year, args.days, dt)
    year = _load(args, args.year, None, dt)
    return year[(year.index >= begin) & (year.index < end)]


def window_label(args) -> str:
    """The window's name in file names: "2024" for a whole year, else e.g. "2025-07-01_30d",
    and "synthetic_demo" for the showcase's synthetic inputs."""
    if getattr(args, "synthetic_demo", False):
        return "synthetic_demo"
    if args.days is None:
        return str(args.year)
    return f"{args.year}-{args.start}_{args.days}d"


def _window_text(args) -> str:
    """The window as the output names it, e.g. "30 days of 2025" or "2024 (whole year)"."""
    if getattr(args, "synthetic_demo", False):
        return "the showcase's 3 synthetic days (demo_05)"
    if args.days is None:
        return f"{args.year} (whole year)"
    text = f"{args.days} {'day' if args.days == 1 else 'days'} of {args.year}"
    return text if args.start == "01-01" else f"{text} from {args.start}"


def _load(args, year: int, days: int | None, dt: float) -> pd.DataFrame:
    """`load_year`, noted in `args.inputs` where a report records what each run loaded."""
    frame = load_year(year, days, dt)
    _note_input(args, f"load_year({year}, days={days}, dt={dt})", frame, year, days)
    return frame


def _note_input(args, label: str, frame: pd.DataFrame, year: int | None = None,
                days: int | None = None) -> None:
    """Record an input frame in `args.inputs`, if the report asked for it (`hybrid.report`).

    label: what loaded it, e.g. "load_year(2024, days=None, dt=0.25)".
    year, days: the calendar year and window length it covers, None where it has no dates.
    """
    inputs = getattr(args, "inputs", None)
    if inputs is not None:
        inputs[label] = {"year": year, "days": days, "frame": frame}


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
    values = market_values(year["price_eur_mwh"], market_value_weights(year), period)
    table = premium_table(index, {"wind": wind, "pv": pv}, values, applicable, period)
    _refuse_unrealised(values.loc[table.index], period)
    return table, premium_rates(index, table, period)


def _refuse_unrealised(values: pd.DataFrame, period: str) -> None:
    """Stop when a period the window is settled on is not over in the data.

    Its market value is then the value of the period so far, which moves until the period
    ends: settled on as the realised value it would overclaim, and no result built on it
    would survive the period's end. datakit caches every payload forever, so a year fetched
    before it ended stays short until its cached payloads are deleted.

    values: `market.market_values` rows of the periods the window is settled on.
    period: "month" or "year".
    Raises ValueError naming each such period and its days of data.
    """
    short = [f"{key} ({row['days']:.0f} days of data)" for key, row in values.iterrows()
             if not row["complete"]]
    if short:
        raise ValueError(
            f"the {'annual' if period == 'year' else 'monthly'} market value of "
            f"{', '.join(short)} is not realised yet: the data does not cover the whole "
            "period. Run a period that is over; if it is over, delete the datakit cache files "
            "of that year, which were fetched before it ended")


def _applicable(market: MarketConfig) -> dict:
    """The plant's applicable values per technology, EUR/MWh."""
    return {"wind": market.anzulegender_wert_wind, "pv": market.anzulegender_wert_pv}


def _premium(args, run: RunConfig, index: pd.DatetimeIndex, wind: np.ndarray,
             pv: np.ndarray) -> tuple:
    """The default plant's premium on the window: table and per-step rate it is settled on.

    B2 plans on the same rate, as perfect foresight may; B0 and B1 read no prices.
    Returns (premium_table frame, rate per step in EUR/MWh).
    """
    year = _load(args, args.year, None, run.dt)
    return _realised_premium(year, index, wind, pv, _applicable(run.market), _DEFAULT_PERIOD)


def _b3_planning_premium(args, run: RunConfig, index: pd.DatetimeIndex) -> np.ndarray:
    """B3's premium per step: the plant's realised monthly rate of the month before.

    A market value is known only once its period is over (`market.planning_rates`), so B3, a
    deployable controller, plans on an estimate: each month on the previous month's realised
    rate. For the default plant, which is paid against the annual value, that is one choice
    among estimates known as early. The previous year's annual value is complete when
    December's monthly one is, and the year's months so far are known from February on.
    ladder prints the rates B3 plans on next to the rate it is settled on, so the gap shows.
    A window starts on 1 January, so its first month needs December of the previous year,
    which this loads.

    args: parsed CLI arguments, e.g. year=2024. run: the run's settings.
    index: the window's timestamps.
    Returns B3's rate per step, EUR/MWh.
    """
    frame = pd.concat([_load(args, args.year - 1, None, run.dt),
                       _load(args, args.year, None, run.dt)])
    wind, pv, _ = to_plant(frame, run.plant.wind_mw, run.plant.pv_mw)
    values = market_values(frame["price_eur_mwh"], market_value_weights(frame), "month")
    monthly = premium_table(frame.index, {"wind": wind, "pv": pv}, values,
                            _applicable(run.market), "month")
    return planning_rates(index, monthly)


def _b3_price_scale(args, run: RunConfig) -> float:
    """What B3's price-forecast error is sized on: the day-ahead price's standard deviation
    over the calendar year before the window, EUR/MWh (`RunConfig.forecast_price_scale`).

    A forecaster sizes its error on prices it has seen, so the window's own must not set it.
    A whole year is steadier than a single month, whose spread a few scarce hours can dominate.

    args: parsed CLI arguments, e.g. year=2024. run: the run's settings.
    Returns the standard deviation in EUR/MWh.
    """
    return float(np.std(_load(args, args.year - 1, None, run.dt)["price_eur_mwh"].to_numpy()))


def _print_premium(table: pd.DataFrame, period: str) -> None:
    """Print a premium table by period: its days of market data, each technology's premium,
    and the plant's rate, a blend weighted by this window's own wind/PV mix."""
    print(f"market premium, settled on the realised {'annual' if period == 'year' else 'monthly'}"
          " market value (a proxy, see negprice), EUR/MWh:")
    for key, t in table.iterrows():
        print(f"  {key} ({t['days']:.0f} days): wind {t['premium_wind']:.2f}, "
              f"PV {t['premium_pv']:.2f}, plant on this window's wind/PV mix {t['rate']:.2f}")


def _premium_columns(index: pd.DatetimeIndex, table: pd.DataFrame, period: str,
                     b3_rate: np.ndarray | None = None) -> dict:
    """The premium a scenario is settled on, as table columns.

    index: the window's timestamps. table: its `market.premium_table`, by `period`.
    b3_rate: the rate B3 planned on per step, where B3 ran.
    Returns the market-value period and the mean over the window's steps of each
    technology's premium and of the plant's rate (EUR/MWh); a window inside one period gets
    that period's values. Where B3 ran, also the mean rate it planned on.
    """
    cols = {"premium_period": period}
    for name, column in (("wind", "premium_wind"), ("pv", "premium_pv"), ("rate", "rate")):
        cols[f"premium_{name}_mean_eur_mwh"] = float(
            np.mean(premium_rates(index, table, period, column)))
    if b3_rate is not None:
        cols["b3_premium_rate_mean_eur_mwh"] = float(np.mean(b3_rate))
    return cols


def _window_columns(args, index: pd.DatetimeIndex, dt: float) -> dict:
    """`tables.window_columns` of a window of Energy-Charts data."""
    return tables.window_columns("Energy-Charts", args.year, len(index), dt, index)


def _write(args, rows: list[dict]) -> None:
    """Write a command's result rows to --out and say where."""
    path = tables.write_csv(rows, args.out)
    print(f"\nwrote {len(rows)} rows to {path}")


def _shown(diff: float, b2: float) -> float:
    """A difference of two rungs' net revenue as printed, EUR: 0 where it lies within the
    ladder's tolerance of B2's revenue `b2` (`ladder_tolerance`), as that much is the solver's
    rounding and printed "-0" where it was negative. The --out tables keep the raw difference.

    diff: e.g. B2 no battery minus B0. b2: B2's net revenue in the same run, EUR.
    """
    return 0.0 if abs(diff) <= ladder_tolerance(b2) else diff


def _report(res: dict, title: str) -> None:
    """Print each rung's settlement: net revenue, export, curtailment chosen and forced by the
    connection, and constraint violations, then each rung's gain over B0."""
    print(f"\n{title}")
    print("=" * len(title))
    hdr = (f"{'controller':<26}{'net EUR':>12}{'export MWh':>12}"
           f"{'curt chosen MWh':>17}{'curt forced MWh':>17}{'viol.':>7}")
    print(hdr)
    print("-" * len(hdr))
    for name, r in res.items():
        c = r["cost"]
        print(f"{name:<26}{c['net_revenue']:>12,.0f}{c['export_mwh']:>12,.0f}"
              f"{c['chosen_curtail_mwh']:>17,.0f}{c['forced_curtail_mwh']:>17,.0f}"
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
    print(f"{len(price)} steps | {_window_text(args)} | "
          f"plant {run.plant.wind_mw:.0f} MW wind + {run.plant.pv_mw:.0f} MWp PV + "
          f"{run.plant.bess_mw:.0f} MW/{run.plant.bess_mwh:.0f} MWh behind "
          f"{run.plant.conn_mw:.0f} MW (overbuild {run.plant.overbuild_ratio:.2f}x)")
    print(describe(wind, pv, price, run.plant.conn_mw, run.dt))
    print(f"negative-price rule: {run.market.negative_price_rule}")
    _print_premium(table, _DEFAULT_PERIOD)
    plan = None
    if not args.fast:
        plan = _b3_planning_premium(args, run, index)
        run = run.with_(forecast_price_scale=_b3_price_scale(args, run))
        by_month = pd.Series(plan, index=index.tz_convert("Europe/Berlin").strftime("%Y-%m"))
        print("B3 plans each month on the realised monthly rate of the month before, EUR/MWh: "
              + ", ".join(f"{m} {r:.2f}" for m, r in by_month.groupby(level=0).first().items()))
        print(f"B3's price-forecast error: standard deviation {run.forecast_sigma:.0%} of "
              f"{run.forecast_price_scale:.2f} EUR/MWh, the day-ahead price's standard "
              f"deviation in {args.year - 1}")
    res = run_ladder(wind, pv, price, run, prem, rebap, include_b3=not args.fast,
                     progress=args.progress, b3_premium_rate=plan)
    _report(res, f"Hybrid plant ladder, {_window_text(args)}")
    if "B3 rolling MPC" in res and "solve_time_s" in res["B3 rolling MPC"]:
        st = res["B3 rolling MPC"]["solve_time_s"]
        print(f"\nB3 solve time: mean {st.mean() * 1000:.0f} ms, max {st.max() * 1000:.0f} ms")
    if args.out:
        _write(args, tables.rung_rows(res, run, _window_columns(args, index, run.dt),
                                      _premium_columns(index, table, _DEFAULT_PERIOD, plan)))


# The rungs sizing prints. B2 minus B0 counts what curtailing at low prices earns as well, which
# needs no battery; "B2 no battery" splits the two.
_SIZING_RUNGS = ("B0 no battery", "B2 no battery", "B2 perfect foresight")


def cmd_sizing(args) -> None:
    """RQ2: what is the optimal degree of connection undersizing?"""
    rows, out = [], []
    # The sweep spans connections BELOW the observed generation peak, because national
    # capacity factors are far smoother than any single site: aggregated across Germany, wind
    # and PV come nowhere near their combined nameplate at once, so a connection near
    # nameplate never binds and the curtailment problem disappears. Sweeping down to 25 % of
    # nameplate is what makes the trade-off visible in this dataset. How far the window's peak
    # sits below nameplate is not quoted here: the command prints it, computed from the
    # Energy-Charts factors it loads, so it holds for whatever year and window are run. On
    # site-level data the same curve would sit much further right - which is precisely why
    # site data is this project's top acquisition priority.
    for conn in (20.0, 25.0, 30.0, 40.0, 60.0):
        plant = PlantConfig(conn_mw=conn)
        run, index, wind, pv, price, rebap = _setup(args, plant=plant)
        table, prem = _premium(args, run, index, wind, pv)
        res = run_ladder(wind, pv, price, run, prem, rebap, include_b3=False,
                         include_b2_no_battery=True)
        for name in _SIZING_RUNGS:
            c = res[name]["cost"]
            rows.append({"conn_MW": conn, "overbuild": plant.overbuild_ratio,
                         "controller": name, "net_EUR": c["net_revenue"],
                         "forced_curt_MWh": c["forced_curtail_mwh"]})
        if args.out:
            net = {name: r["cost"]["net_revenue"] for name, r in res.items()}
            out += tables.rung_rows(
                res, run, _window_columns(args, index, run.dt),
                _premium_columns(index, table, _DEFAULT_PERIOD), conn_mw=conn,
                overbuild=plant.overbuild_ratio,
                battery_value_eur=net["B2 perfect foresight"] - net["B2 no battery"],
                chosen_curtailment_value_eur=net["B2 no battery"] - net["B0 no battery"])
    df = pd.DataFrame(rows)
    # generation and installed ratings are the same at every connection size
    installed = run.plant.installed_mw
    peak = float(np.max(wind + pv))
    print(f"\nConnection-capacity sweep ({run.plant.wind_mw:.0f} MW wind + "
          f"{run.plant.pv_mw:.0f} MWp PV installed)")
    print(f"peak combined output in the window: {peak:.1f} MW, {peak / installed:.3f} of "
          "installed (national Energy-Charts factors)")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    piv = df.pivot(index="conn_MW", columns="controller", values="net_EUR")[list(_SIZING_RUNGS)]
    b2 = piv["B2 perfect foresight"]
    for column, diff in (("battery_value_EUR", b2 - piv["B2 no battery"]),
                         ("chosen_curtailment_value_EUR",
                          piv["B2 no battery"] - piv["B0 no battery"])):
        piv[column] = [_shown(d, r) for d, r in zip(diff, b2)]
    print("\nValue by connection size: the battery's own (B2 - B2 no battery), and that of "
          "curtailing\nat low prices, which needs no battery (B2 no battery - B0):")
    print(piv.to_string(float_format=lambda x: f"{x:,.0f}"))
    print("(a difference within the solver's rounding prints as 0; --out keeps it as solved)")
    if args.out:
        _write(args, out)


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
    year = _load(args, args.year, None, dt)
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
    rows, out = [], []
    for aw_wind, aw_pv, label, period in _NEGPRICE_VINTAGES:
        for rule in rules:
            run = base.with_(market=_vintage_market(aw_wind, aw_pv, rule))
            res = run_ladder(wind, pv, price, run, premiums[label][1], rebap, include_b3=False)
            for name, r in res.items():
                rows.append({"vintage": label, "rule": rule, "controller": name,
                             "net_EUR": r["cost"]["net_revenue"],
                             "chosen_curt_MWh": r["cost"]["chosen_curtail_mwh"]})
            if args.out:
                table = premiums[label][0]
                out += tables.rung_rows(res, run, _window_columns(args, index, run.dt),
                                        _premium_columns(index, table, period), vintage=label,
                                        rule=rule, no_premium=_no_premium(table))
    _print_negprice(args.year, by_vintage, rules, premiums, pd.DataFrame(rows))
    if args.out:
        _write(args, out)


def _no_premium(table: pd.DataFrame) -> str:
    """Each technology and period of a premium table that pays no premium, e.g. "PV in 2025",
    or "none"."""
    zero = [f"{name} in {key}" for key, t in table.iterrows()
            for tech, name in (("wind", "wind"), ("pv", "PV"))
            if t[f"premium_{tech}"] <= 0]
    return ", ".join(zero) or "none"


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
                  f"PV {t['premium_pv']:.2f}, plant on this window's mix {t['rate']:.2f}")
        g = df[df["vintage"] == label]
        print(g.pivot(index="controller", columns="rule", values="net_EUR")[rules]
              .to_string(float_format=lambda x: f"{x:,.0f}"))
        print("  self-chosen curtailment (MWh), should RISE as the rule tightens:")
        print(g.pivot(index="controller", columns="rule", values="chosen_curt_MWh")[rules]
              .to_string(float_format=lambda x: f"{x:,.1f}"))
        no_premium.append(f"{label} {_no_premium(table)}")
    print(f"\nNote: a technology whose market value reaches its applicable value gets no "
          f"premium, and there the rule cannot bite - there is no premium to suspend. "
          f"No premium in this window ({year}): {'; '.join(no_premium)}.")


def cmd_decompose(args) -> None:
    """RQ1: is the battery's value curtailment avoidance, or arbitrage?"""
    run, index, wind, pv, price, rebap = _setup(args)
    table, prem = _premium(args, run, index, wind, pv)
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
    share = (v_full - v_arbitrage) / v_full if abs(v_full) > 1e-9 else None
    if share is not None:
        print(f"  curtailment share: {share * 100:5.1f}%")
    if args.out:
        # the split is a result of both connections, so it repeats on every row
        split = {"value_as_built_eur": v_full, "value_unconstrained_eur": v_arbitrage,
                 "attributable_to_curtailment_eur": v_full - v_arbitrage,
                 "curtailment_share": share}
        window = _window_columns(args, index, run.dt)
        # both connections see the same generation, so they are settled on the same premium
        premium = _premium_columns(index, table, _DEFAULT_PERIOD)
        _write(args, [row for scenario, r, res in (("as built", run, base),
                                                   ("unconstrained connection", run2, nocurt))
                      for row in tables.rung_rows(res, r, window, premium, scenario=scenario,
                                                  **split)])


# The B3 settings the ablation crosses: perfect forecasts and a forecast error of 15 %, each
# without and with a linear terminal value on stored energy (`default_terminal_price`).
_ABLATION = tuple((sigma, terminal) for sigma in (0.0, 0.15) for terminal in (False, True))


def _ablation_inputs(args) -> tuple:
    """The b3-ablation's window: the showcase's synthetic inputs, or Energy-Charts data.

    args: parsed CLI arguments, e.g. synthetic_demo=False, year=2025, days=30.
    Returns (run, wind, pv, price, rebap, premium_rate, b3_premium_rate, window columns,
    premium columns). On real data every rung is settled on the realised annual value and B3
    plans on the month before's realised rate, as in `ladder`; the demo has one fixed rate.
    """
    if args.synthetic_demo:
        demo = synthetic.demo_inputs()
        _note_input(args, "synthetic.demo_inputs()", pd.DataFrame(
            {"wind_mw": demo.wind, "pv_mw": demo.pv, "price_eur_mwh": demo.price}))
        window = tables.window_columns("synthetic (showcase demo_05)", None, len(demo.price),
                                       demo.run.dt)
        premium = {"premium_period": "fixed", "premium_wind_mean_eur_mwh": None,
                   "premium_pv_mean_eur_mwh": None, "premium_rate_mean_eur_mwh": demo.premium_rate,
                   "b3_premium_rate_mean_eur_mwh": demo.premium_rate}
        return (demo.run, demo.wind, demo.pv, demo.price, None, demo.premium_rate, None,
                window, premium)
    run, index, wind, pv, price, rebap = _setup(args)
    table, rate = _premium(args, run, index, wind, pv)
    plan = _b3_planning_premium(args, run, index)
    run = run.with_(forecast_price_scale=_b3_price_scale(args, run))
    return (run, wind, pv, price, rebap, rate, plan, _window_columns(args, index, run.dt),
            _premium_columns(index, table, _DEFAULT_PERIOD, plan))


def _recovery(net: dict) -> float | None:
    """B3's share of the headroom from B1 to B2, (B3 - B1) / (B2 - B1), from net revenue in
    EUR by rung; None where B2 is not above B1 by more than the ladder's tolerance."""
    b1, b2, b3 = (net[name] for name in ("B1 curtailment avoidance", "B2 perfect foresight",
                                         "B3 rolling MPC"))
    return None if b2 - b1 <= ladder_tolerance(b2) else (b3 - b1) / (b2 - b1)


def cmd_b3_ablation(args) -> None:
    """B3 under perfect forecasts and a 15 % forecast error, without and with a terminal
    value: B0 to B3, B3's recovery of the B1-to-B2 headroom, and its stored energy at the end."""
    run, wind, pv, price, rebap, rate, plan, window, premium = _ablation_inputs(args)
    rows, out = [], []
    for sigma, terminal in _ABLATION:
        case = run.with_(forecast_sigma=sigma, terminal_value=terminal)
        res = run_ladder(wind, pv, price, case, rate, rebap, progress=args.progress,
                         b3_premium_rate=plan)
        net = {name: r["cost"]["net_revenue"] for name, r in res.items()}
        recovery = _recovery(net)
        rows.append({"forecast_sigma": sigma, "terminal_value": terminal,
                     **{f"{name[:2]} EUR": value for name, value in net.items()},
                     "recovery": recovery,
                     "B3 end SoC MWh": float(res["B3 rolling MPC"]["soc"][-1])})
        if args.out:
            out += tables.rung_rows(res, case, window, premium, forecast_sigma=sigma,
                                    terminal_value=terminal, recovery=recovery)
    title = f"B3 ablation, forecast error x terminal value, {_window_text(args)}"
    print(f"\n{title}\n{'=' * len(title)}")
    print(pd.DataFrame(rows).to_string(index=False, formatters={
        **{f"B{k} EUR": "{:,.0f}".format for k in range(4)},
        "recovery": lambda x: "n/a" if x is None or np.isnan(x) else f"{x:.1%}",
        "B3 end SoC MWh": "{:.1f}".format}))
    print("recovery: (B3 - B1) / (B2 - B1), B3's share of the headroom from B1 to B2")
    if args.out:
        _write(args, out)


def _month_day(text: str) -> str:
    """argparse type of --start: a calendar day as MM-DD, e.g. "07-01"."""
    try:
        day = datetime.strptime(f"2000-{text}", "%Y-%m-%d")     # a leap year admits 02-29
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"not a day as MM-DD: {text!r}") from err
    return f"{day.month:02d}-{day.day:02d}"


def _days(text: str) -> int:
    """argparse type of --days: a whole number of days, at least 1, e.g. "30".

    load_year cuts a window with iloc[:steps], so a negative count kept all but the last days
    of the year without a word, and 0 kept nothing and failed later on an unrelated error.
    """
    try:
        days = int(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"not a whole number of days: {text!r}") from err
    if days < 1:
        raise argparse.ArgumentTypeError(f"a window needs at least 1 day, not {days}")
    return days


def build_parser() -> argparse.ArgumentParser:
    """The command line: one subcommand per experiment, each with its window and settings."""
    p = argparse.ArgumentParser(prog="hybrid", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("ladder", cmd_ladder), ("sizing", cmd_sizing),
                     ("negprice", cmd_negprice), ("decompose", cmd_decompose),
                     ("b3-ablation", cmd_b3_ablation)):
        sp = sub.add_parser(name)
        sp.add_argument("--year", type=int, default=2025)
        span = sp.add_mutually_exclusive_group()
        span.add_argument("--days", type=_days, default=30, help="window length in days of 24 h")
        span.add_argument("--full-year", dest="days", action="store_const", const=None,
                          help="the whole calendar year in Berlin time")
        sp.add_argument("--start", type=_month_day, default="01-01",
                        help="first day of the window, MM-DD in Berlin time")
        sp.add_argument("--horizon", type=int, default=96)
        sp.add_argument("--neg-rule", default="new_2025", choices=NEGATIVE_PRICE_RULES)
        if name == "b3-ablation":
            sp.add_argument("--synthetic-demo", action="store_true",
                            help="run on showcase.py demo_05's synthetic inputs and settings "
                                 "instead of the window and plant options")
        else:
            sp.add_argument("--fast", action="store_true")
        sp.add_argument("--progress", action="store_true")
        sp.add_argument("--out", metavar="PATH",
                        help="also write the result as a CSV, one row per scenario and rung")
        sp.set_defaults(func=fn)
    rp = sub.add_parser("report", help="run the published experiments into reports/")
    rp.add_argument("--only", nargs="+", metavar="EXPERIMENT",
                    help="run only these experiments, e.g. sizing_2024; processes running "
                         "different ones can work side by side")
    rp.add_argument("--reports-dir", metavar="PATH",
                    help="write there instead of the project's reports/ folder")
    rp.set_defaults(func=_cmd_report)
    return p


def _cmd_report(args) -> None:
    """The report command. `hybrid.report` runs the other commands, so it is imported here,
    when the command runs, rather than when this module loads."""
    from .report import cmd_report

    cmd_report(args)


def main(argv: list[str] | None = None) -> int:
    """Run one command; argv defaults to the command line. Returns the exit status, 0."""
    argv = sys.argv[1:] if argv is None else list(argv)
    args = build_parser().parse_args(argv)
    args.argv = argv          # the report records how it was started
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

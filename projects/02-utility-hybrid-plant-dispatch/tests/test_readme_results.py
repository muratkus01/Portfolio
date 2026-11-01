"""The README's results against the committed tables in reports/.

Each result table in the README must hold what its CSV holds, cell by cell, to the precision
it prints, under the column names it prints; the claims it makes about the tables must hold
in them; its key results may quote only numbers those tables print, each in the sentence of
its own year, window and rung; and the settings its prose quotes must be those the tables ran
with. The README's figures once came from console runs that no file kept, and outlived the
code that made them. Offline: this reads files, asks git which files it tracks, and lets
pytest count the suite without running it.
"""
from __future__ import annotations

import posixpath
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hybrid.market import vintage_rules

PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
B0, B1, B2, B3 = ("B0 no battery", "B1 curtailment avoidance", "B2 perfect foresight",
                  "B3 rolling MPC")
DECOMPOSE = ("decompose_2024.csv", "decompose_2024_conn-30.csv", "decompose_2025.csv",
             "decompose_2025_conn-30.csv")
LADDER = ("ladder_2025-01-01_30d.csv", "ladder_2025-07-01_30d.csv")
# Each result table, by the caption on the line above it, and the CSVs that caption links.
TABLES = {
    "Windows of the tables": ("window_stats.csv",),
    "Connection sweep, calendar year 2024": ("sizing_2024.csv",),
    "Connection sweep, calendar year 2025": ("sizing_2025.csv",),
    "The battery's value by connection": DECOMPOSE,
    "Premium by vintage": ("negprice_2024.csv", "negprice_2025.csv",
                           "negprice_2024_premium.csv", "negprice_2025_premium.csv"),
    "Negative-price rule, calendar year 2024": ("negprice_2024.csv",),
    "Negative-price rule, calendar year 2025": ("negprice_2025.csv",),
    "B0's loss of premium from `none`": ("negprice_2024.csv", "negprice_2025.csv"),
    "B3 on 30 days of Energy-Charts data": LADDER,
    "B3 ablation on synthetic inputs": ("b3_ablation_synthetic_demo.csv",),
}
# A number as the README prints it, e.g. "1,240,768", "-2.7" or "0.00", but not the digits of
# a name such as B3, RQ1 or new_2025.
NUMBER = re.compile(r"(?<![\w.])-?\d(?:[\d,]*\d)?(?:\.\d+)?(?!\w)")


def readme() -> str:
    """The README's text, with any Unicode minus sign as an ASCII one."""
    return (PROJECT / "README.md").read_text(encoding="utf-8").replace("−", "-")


def report(name: str) -> pd.DataFrame:
    """A committed table of reports/, e.g. report("sizing_2024.csv")."""
    return pd.read_csv(REPORTS / name)


def table(caption: str) -> tuple[list[str], list[list[str]]]:
    """The markdown table below the one README line that holds `caption`, and that line must
    link the table's CSVs (TABLES), or nothing for a caption not in TABLES.

    caption: e.g. "Connection sweep, calendar year 2024".
    Returns (header cells, rows of cells), each cell's text without markup: a link keeps its
    text, and bold and code marks go.
    """
    lines = readme().splitlines()
    at = [i for i, line in enumerate(lines) if caption in line and not line.startswith("|")]
    assert len(at) == 1, f"the README needs one line with {caption!r}, not {len(at)}"
    for source in TABLES.get(caption, ()):
        assert f"(reports/{source})" in lines[at[0]], f"{caption!r} must link {source}"
    i = at[0] + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    cells = []
    while i < len(lines) and lines[i].startswith("|"):
        cells.append([_plain(cell) for cell in lines[i].strip().strip("|").split("|")])
        i += 1
    assert len(cells) > 2, f"no table follows {caption!r}"
    return cells[0], cells[2:]


def _plain(cell: str) -> str:
    """A table cell's text without markdown: "[`x.csv`](reports/x.csv)" becomes "x.csv"."""
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    return cell.replace("**", "").replace("`", "").strip()


def assert_row(cells: list[str], expected: list, where: str) -> None:
    """One README row holds `expected`: text exactly, and a number as the cell prints it,
    within half a unit of its last printed digit.

    cells: the row's cell texts. expected: per cell a str, or a number in the cell's unit, e.g.
    94.147 for "94.1 %". where: names the row in a failure, e.g. "sizing 2024, 20 MW".
    """
    assert len(cells) == len(expected), f"{where}: {cells} against {expected}"
    for cell, want in zip(cells, expected):
        if isinstance(want, str):
            assert cell == want, f"{where}: {cell!r}, expected {want!r}"
            continue
        text = cell.removesuffix(" %")
        assert re.fullmatch(r"-?\d{1,3}(,\d{3})*(\.\d+)?", text), f"{where}: {cell!r}"
        value = float(text.replace(",", ""))
        assert not (value == 0 and text.startswith("-")), f"{where}: {cell!r} prints -0"
        unit = 10.0 ** -len(text.partition(".")[2])
        assert abs(value - want) <= unit / 2 + 1e-9 * abs(want), (
            f"{where}: {cell!r} does not print {want}")


def window_name(row: pd.Series) -> str:
    """The README's name of a table's window, from the row's window columns: "calendar year
    2024", "1 to 30 July 2025", or for the dateless demo "3 synthetic days"."""
    if pd.isna(row["window_start_berlin"]):
        return f"{row['days']:.0f} synthetic days"
    first = pd.Timestamp(row["window_start_berlin"])
    last = pd.Timestamp(row["window_end_berlin"]) - pd.Timedelta(days=1)
    if (first.month, first.day, last.month, last.day) == (1, 1, 12, 31):
        return f"calendar year {first.year}"
    if first.month == last.month:
        return f"{first.day} to {last.day} {last.month_name()} {last.year}"
    return f"{first.day} {first.month_name()} to {last.day} {last.month_name()} {last.year}"


def test_each_table_names_the_window_and_command_that_made_it():
    """The commands the README prints are those run_meta.csv records, one per experiment, and
    every CSV in reports/ is accounted for."""
    meta = report("run_meta.csv")
    commands = meta[(meta["section"] == "run") & (meta["item"] == "command")].set_index(
        "experiment")["value"]
    header, rows = table("How each table is made")
    assert header == ["Table", "Window, Berlin time", "Command"]
    named = {files.split(",")[0].removesuffix(".csv"): (files, window, command)
             for files, window, command in rows}
    assert sorted(named) == sorted(commands.index)
    listed = {"window_stats.csv", "run_meta.csv"}
    for experiment, (files, window, command) in named.items():
        assert command == f"python -m hybrid.cli {commands[experiment]}", experiment
        assert window == window_name(report(f"{experiment}.csv").iloc[0]), experiment
        listed |= set(files.split(", "))
    assert listed == {path.name for path in REPORTS.glob("*.csv")}


def test_the_window_table_quotes_window_stats():
    """Per window: quarter-hours, mean price, negative-price quarter-hours, peak generation,
    the largest combined capacity factor, and how often generation exceeds the connection."""
    expected = {window_name(w): [window_name(w), w["steps"], w["mean_price_eur_mwh"],
                                 w["negative_price_quarter_hours"], w["peak_generation_mw"],
                                 w["max_combined_cf"], w["connection_mw"],
                                 w["steps_over_connection"]]
                for _, w in report("window_stats.csv").iterrows()}
    header, rows = table("Windows of the tables")
    assert header == ["Window, Berlin time", "Quarter-hours", "Mean day-ahead price EUR/MWh",
                      "Quarter-hours with a negative price", "Peak generation MW",
                      "Largest combined capacity factor", "Connection MW",
                      "Quarter-hours above the connection"]
    assert sorted(cells[0] for cells in rows) == sorted(expected)
    for cells in rows:
        assert_row(cells, expected[cells[0]], cells[0])


@pytest.mark.parametrize("year", [2024, 2025])
def test_the_sizing_table_quotes_its_csv(year):
    """Per connection: B0, B1, B2, the battery's own value, curtailing at low prices and B0's
    forced curtailment. The README says the battery's value rises with every step of
    connection size, and that B1 equals B0 where no generation exceeds the connection."""
    header, rows = table(f"Connection sweep, calendar year {year}")
    assert header == ["Connection MW", "Overbuild", "B0 EUR", "B1 EUR", "B2 EUR",
                      "The battery's own value EUR", "Curtailing at low prices EUR",
                      "B0 forced curtailment MWh"]
    groups = [(conn, g.set_index("rung")) for conn, g in report(f"sizing_{year}.csv")
              .groupby("conn_mw")]
    assert len(rows) == len(groups)
    for cells, (conn, g) in zip(rows, groups):
        net = g["net_revenue_eur"]
        assert_row(cells, [conn, g["overbuild"].iloc[0], net[B0], net[B1], net[B2],
                           g["battery_value_eur"].iloc[0],
                           g["chosen_curtailment_value_eur"].iloc[0],
                           g.loc[B0, "forced_curtail_mwh"]], f"sizing {year}, {conn:g} MW")
        if g.loc[B0, "forced_curtail_mwh"] == 0:
            assert net[B1] == pytest.approx(net[B0], abs=0.01), f"{conn:g} MW"
    battery = [g["battery_value_eur"].iloc[0] for _, g in groups]
    assert all(a < b for a, b in zip(battery, battery[1:]))


def test_the_decompose_table_quotes_its_csvs():
    """Per year and connection as built: the battery's value there and at the 200 MW that
    nothing reaches, and the connection limit's effect, the difference, as a share."""
    header, rows = table("The battery's value by connection")
    wide_mw = {report(name).set_index("scenario").loc["unconstrained connection", "plant_conn_mw"]
               .iloc[0] for name in DECOMPOSE}
    assert len(wide_mw) == 1
    assert header == ["Year", "Connection as built MW", "The battery's value as built EUR",
                      f"The battery's value at {wide_mw.pop():.0f} MW EUR",
                      "Connection limit's effect EUR", "Share of the value as built"]
    assert len(rows) == len(DECOMPOSE)
    for cells, name in zip(rows, DECOMPOSE):
        d = report(name).set_index("scenario")
        built, wide = d.loc["as built"].iloc[0], d.loc["unconstrained connection"].iloc[0]
        assert_row(cells, [str(built["year"]), built["plant_conn_mw"],
                           built["battery_value_eur"], wide["battery_value_eur"],
                           built["connection_limit_effect_eur"],
                           100 * built["connection_limit_share"]], name)


def test_the_premium_table_quotes_the_negprice_csvs():
    """Per year and vintage: the market-value basis, the annual proxy where it applies, and
    the mean premium per technology and for the plant, with the periods that pay none."""
    header, rows = table("Premium by vintage")
    assert header == ["Year", "Vintage", "Market value", "Wind market value EUR/MWh",
                      "PV market value EUR/MWh", "Wind premium EUR/MWh", "PV premium EUR/MWh",
                      "Plant rate EUR/MWh", "No premium for"]
    expected = []
    for year in (2024, 2025):
        periods = report(f"negprice_{year}_premium.csv").set_index("vintage")
        for vintage, g in report(f"negprice_{year}.csv").groupby("vintage", sort=False):
            first = g.iloc[0]
            if first["premium_period"] == "year":
                annual = periods.loc[vintage]
                basis = ["annual", annual["market_value_wind_eur_mwh"],
                         annual["market_value_pv_eur_mwh"]]
            else:
                basis = ["monthly", "by month", "by month"]
            expected.append([str(year), vintage, *basis, first["premium_wind_mean_eur_mwh"],
                             first["premium_pv_mean_eur_mwh"],
                             first["premium_rate_mean_eur_mwh"], first["no_premium"]])
    assert len(rows) == len(expected)
    for cells, want in zip(rows, expected):
        assert_row(cells, want, f"premium {want[0]}, {want[1]}")


@pytest.mark.parametrize("year", [2024, 2025])
def test_the_negprice_table_quotes_its_csv(year):
    """Per rule, the vintage that carries it (market.vintage_rules) and, for the recent and
    the older vintage, B0, B2 and B2's chosen curtailment. The README says B1 equals B0 in
    every row; that each tightening lowers B0 and raises B2's chosen curtailment; and that B2
    loses less than B0 to every rule."""
    d = report(f"negprice_{year}.csv")
    header, rows = table(f"Negative-price rule, calendar year {year}")
    recent, older = dict.fromkeys(d["vintage"])
    assert "recent" in recent and "older" in older
    assert header == ["Rule", "Vintage that carries it", "Recent: B0 EUR", "Recent: B2 EUR",
                      "Recent: B2 chosen curtailment MWh", "Older: B0 EUR", "Older: B2 EUR",
                      "Older: B2 chosen curtailment MWh"]
    rules = list(dict.fromkeys(d["rule"]))
    carried = {rule: _as_carried_in(vintage, year) for vintage, rule in vintage_rules(year).items()}
    assert len(rows) == len(rules)
    for cells, rule in zip(rows, rules):
        want = [rule, carried[rule]]
        for vintage in (recent, older):
            g = d[(d["rule"] == rule) & (d["vintage"] == vintage)].set_index("rung")
            assert g.loc[B1, "net_revenue_eur"] == pytest.approx(g.loc[B0, "net_revenue_eur"],
                                                                 abs=0.01)
            want += [g.loc[B0, "net_revenue_eur"], g.loc[B2, "net_revenue_eur"],
                     g.loc[B2, "chosen_curtail_mwh"]]
        assert_row(cells, want, f"negprice {year}, {rule}")
    for vintage in (recent, older):
        by_rule = d[d["vintage"] == vintage].pivot(
            index="rule", columns="rung", values=["net_revenue_eur", "chosen_curtail_mwh"]
        ).loc[rules]
        b0, b2 = by_rule[("net_revenue_eur", B0)], by_rule[("net_revenue_eur", B2)]
        assert (np.diff(b0) < 0).all(), vintage
        assert (np.diff(by_rule[("chosen_curtail_mwh", B2)]) > 0).all(), vintage
        assert ((b2.iloc[0] - b2.iloc[1:]) < (b0.iloc[0] - b0.iloc[1:])).all(), vintage


def test_the_per_technology_table_quotes_the_negprice_csvs():
    """Per year and vintage, B0's loss of premium from none to each rule: at the blended rate
    every rung is settled on, and paid per technology on all of its generation, which B0
    exports here. The README says the two agree under none; that per technology every rule
    removes more, from and to the shares it quotes; and that PV's mean premium is above
    wind's in every row of the premium table."""
    header, rows = table("B0's loss of premium from `none`")
    expected, more = [], {}
    for year in (2024, 2025):
        d = report(f"negprice_{year}.csv")
        rules = list(dict.fromkeys(d["rule"]))
        assert header == ["Year", "Vintage", "Paid", *[f"{rule} EUR" for rule in rules[1:]]]
        b0 = d[d["rung"] == B0].set_index(["vintage", "rule"])
        assert (b0["forced_curtail_mwh"] == 0).all() and (b0["chosen_curtail_mwh"] == 0).all()
        for vintage in dict.fromkeys(d["vintage"]):
            blended = b0.loc[vintage, "premium_revenue_eur"]
            exact = b0.loc[vintage, "full_export_premium_by_technology_eur"]
            assert exact["none"] == pytest.approx(blended["none"]), (year, vintage)
            loss = {"at the blended rate": blended["none"] - blended[rules[1:]],
                    "per technology": exact["none"] - exact[rules[1:]]}
            expected += [[str(year), vintage, paid, *lost] for paid, lost in loss.items()]
            short = f"{'older' if 'older' in vintage else 'recent'} vintage, {year}"
            for rule in rules[1:]:
                more[short, rule] = loss["per technology"][rule] / loss["at the blended rate"][rule]
            means = b0.loc[vintage].iloc[0]
            assert means["premium_pv_mean_eur_mwh"] > means["premium_wind_mean_eur_mwh"]
    assert len(rows) == len(expected)
    for cells, want in zip(rows, expected):
        assert_row(cells, want, f"per technology {want[0]}, {want[1]}, {want[2]}")
    (low, _), (high, _) = min(more, key=more.get), max(more, key=more.get)
    lo, hi = 100 * (min(more.values()) - 1), 100 * (max(more.values()) - 1)
    assert lo > 0
    text = " ".join(readme().split())
    assert f"from {lo:.0f} % more ({low}) to {hi:.0f} % more ({high})" in text
    quoted = re.findall(r"(\d+) % to (\d+) %", text)
    assert quoted and set(quoted) == {(f"{lo:.0f}", f"{hi:.0f}")}, quoted


def _as_carried_in(vintage: str, year: int) -> str:
    """A vintage as the negprice tables name it: one that begins after the evaluated year, as
    "from 25 Feb 2025" does in 2024, is marked a counterfactual there."""
    begins = re.fullmatch(r"from .* (\d{4})", vintage)
    if begins and int(begins.group(1)) > year:
        return f"{vintage} (counterfactual in {year})"
    return vintage


def test_the_ladder_table_quotes_its_csvs():
    """Per 30-day window: B0 to B3, B3's share of the gain from B1 to B2, and the premium
    every rung is settled on against the one B3 planned on."""
    header, rows = table("B3 on 30 days of Energy-Charts data")
    assert header == ["Window, Berlin time", "B0 EUR", "B1 EUR", "B2 EUR", "B3 EUR",
                      "B3's share of the B1-to-B2 gain", "Premium settled on EUR/MWh",
                      "Premium B3 planned on EUR/MWh"]
    assert len(rows) == len(LADDER)
    for cells, name in zip(rows, LADDER):
        d = report(name).set_index("rung")
        net, b3 = d["net_revenue_eur"], d.loc[B3]
        assert_row(cells, [window_name(b3), net[B0], net[B1], net[B2], net[B3],
                           100 * (net[B3] - net[B1]) / (net[B2] - net[B1]),
                           b3["premium_rate_mean_eur_mwh"],
                           b3["b3_premium_rate_mean_eur_mwh"]], name)


def test_the_ablation_table_quotes_its_csv():
    """Per case of forecast error and terminal value: B0 to B3, B3's recovery and the energy
    B3 holds at the end, all on the showcase's synthetic inputs."""
    d = report("b3_ablation_synthetic_demo.csv")
    assert (d["inputs"] == "synthetic (showcase demo_05)").all()
    header, rows = table("B3 ablation on synthetic inputs")
    assert header == ["Forecast error", "Terminal value", "B0 EUR", "B1 EUR", "B2 EUR", "B3 EUR",
                      "B3's share of the B1-to-B2 gain", "Energy B3 holds at the end MWh"]
    cases = list(d.groupby(["b3_forecast_sigma", "b3_terminal_value"], sort=False))
    assert len(rows) == len(cases)
    for cells, ((sigma, terminal), g) in zip(rows, cases):
        net, b3 = g.set_index("rung")["net_revenue_eur"], g.set_index("rung").loc[B3]
        assert_row(cells, [100 * sigma, "on" if terminal else "off", net[B0], net[B1],
                           net[B2], net[B3], 100 * b3["recovery"], b3["final_soc_mwh"]],
                   f"sigma {sigma}, terminal value {terminal}")


def test_the_key_results_quote_only_numbers_the_tables_print():
    """A figure in the key results that no result table prints traces to no CSV."""
    printed = {number for caption in TABLES for row in table(caption)[1] for cell in row
               for number in NUMBER.findall(cell)}
    text = readme()
    block = text.split("### Key results", 1)[-1].split("\n#", 1)[0]
    assert "### Key results" in text and NUMBER.findall(block)
    stray = [n for n in NUMBER.findall(block) if n not in printed | {"2024", "2025"}]
    assert not stray, f"key results quote numbers no table prints: {stray}"


def test_no_row_breaks_a_limit_or_settles_imbalance_or_imports():
    """The README says so of every table: no rung commits a schedule or buys grid energy."""
    tables = [pd.read_csv(path) for path in sorted(REPORTS.glob("*.csv"))]
    rungs = [d for d in tables if "violations" in d.columns]
    assert len(rungs) == 11
    for d in rungs:
        assert (d["violations"] == 0).all()
        assert (d["imbalance_cost_eur"] == 0).all() and (d["import_cost_eur"] == 0).all()


def test_the_rl_comparison_quotes_project_01s_table():
    """The scope note's figures, each in its own place: Project 01's SAC, the median of its
    seeds, against its B3. Swapped, the two would invert the reason for stopping at B3."""
    path = Path("..", "01-prosumer-pv-bess-mpc-rl", "reports", "rl_eval_H28", "rl_summary.csv")
    capture = pd.read_csv(PROJECT / path, index_col=0)["capture of B1->B2 %"]
    seeds = capture[capture.index.str.startswith("RL SAC")]
    b3 = capture[capture.index.str.startswith("B3")].item()
    text = prose()
    assert f"({path.as_posix()})" in text
    assert (f"a soft actor-critic agent reaches {seeds.median():.1f} % of the gain from B1 to B2 "
            f"(median of {WORDS[len(seeds)]} seeds), against {b3:.1f} % for its rolling MPC"
            ) in text


# Counts the README spells out.
WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def prose() -> str:
    """The README's text on one line: every run of whitespace one space, and bold marks and
    the marks that open a blockquote's lines gone, so a sentence reads the same wherever its
    lines wrap."""
    lines = (re.sub(r"^\s*>\s?", "", line) for line in readme().splitlines())
    return " ".join(" ".join(lines).replace("**", "").split())


def key_results() -> str:
    """The key results block, as `prose` gives it."""
    text = readme()
    assert "### Key results" in text
    block = text.split("### Key results", 1)[1].split("\n#", 1)[0]
    return " ".join(block.replace("**", "").split())


def test_each_key_result_sits_in_its_sentence():
    """A key result must hold where its sentence puts it: in its year, window, connection and
    rung. A number printed in some table can still sit in the wrong sentence, e.g. January's
    recovery quoted for July."""
    text, years = key_results(), (2024, 2025)
    sizing = {y: report(f"sizing_{y}.csv").groupby("conn_mw").first() for y in years}
    own = {y: sizing[y]["battery_value_eur"] for y in years}
    low = {y: sizing[y]["chosen_curtailment_value_eur"] for y in years}
    lo, hi = own[2024].index.min(), own[2024].index.max()
    # the sizing test checks that it rises at every step, in each year
    assert "rises with every step of connection size in both years" in text
    assert (f"from {own[2024][lo]:,.0f} EUR at {lo:.0f} MW to {own[2024][hi]:,.0f} EUR at "
            f"{hi:.0f} MW in 2024, and from {own[2025][lo]:,.0f} to {own[2025][hi]:,.0f} EUR "
            f"in 2025") in text
    assert (f"adds {low[2024].min():,.0f} to {low[2024].max():,.0f} EUR in 2024 and "
            f"{low[2025].min():,.0f} to {low[2025].max():,.0f} EUR in 2025") in text

    windows = report("window_stats.csv").set_index("window").loc[[str(y) for y in years]]
    assert (windows["steps_over_connection"] == 0).all()
    assert f"The {windows['connection_mw'].iloc[0]:.0f} MW connection never binds" in text
    for y in years:
        wide = report(f"decompose_{y}.csv").set_index("scenario").loc["as built"].iloc[0]
        assert abs(wide["connection_limit_effect_eur"]) <= wide["ladder_tolerance_eur"]
    tight = {y: report(f"decompose_{y}_conn-30.csv").set_index("scenario").loc["as built"]
             .iloc[0] for y in years}
    mw = tight[2024]["plant_conn_mw"]
    effect = {y: tight[y]["connection_limit_effect_eur"] for y in years}
    share = {y: 100 * tight[y]["connection_limit_share"] for y in years}
    assert (f"A {mw:.0f} MW connection changes it by {effect[2024]:,.0f} EUR in 2024 and "
            f"{effect[2025]:,.0f} EUR in 2025 ({share[2024]:.1f} % and {share[2025]:.1f} % of "
            f"the battery's value at {mw:.0f} MW)") in text

    b3 = [report(name).set_index("rung") for name in LADDER]
    month = [pd.Timestamp(d.loc[B3, "window_start_berlin"]) for d in b3]
    recovery = [100 * (d.loc[B3, "net_revenue_eur"] - d.loc[B1, "net_revenue_eur"])
                / (d.loc[B2, "net_revenue_eur"] - d.loc[B1, "net_revenue_eur"]) for d in b3]
    assert (f"recovers {recovery[0]:.1f} % ({month[0].month_name()} {month[0].year}) and "
            f"{recovery[1]:.1f} % ({month[1].month_name()} {month[1].year}) of the gain from "
            f"B1 to B2") in text
    sigma = {d.loc[B3, "b3_forecast_sigma"] for d in b3}
    assert len(sigma) == 1 and {d.loc[B3, "b3_seed"] for d in b3} == {0}
    assert f"the truth with a {100 * sigma.pop():.0f} % AR(1) error in one random draw" in text
    planned = [d.loc[B3, "b3_premium_rate_mean_eur_mwh"] for d in b3]
    settled = [d.loc[B3, "premium_rate_mean_eur_mwh"] for d in b3]
    assert (f"It plans on the previous month's realised premium, {planned[0]:.2f} and "
            f"{planned[1]:.2f} EUR/MWh, and is settled on {settled[0]:.2f} and "
            f"{settled[1]:.2f} EUR/MWh") in text

    negprice = {y: report(f"negprice_{y}.csv") for y in years}
    recent = {y: negprice[y][negprice[y]["vintage"].str.contains("recent")
                             & (negprice[y]["rule"] == "new_2025")].set_index("rung")
              for y in years}
    for y in years:
        assert recent[y].loc[B2, "market_anzulegender_wert_pv"] == (
            sizing[y]["market_anzulegender_wert_pv"].iloc[0]), "the default plant"
    curtailed = {y: recent[y].loc[B2, "chosen_curtail_mwh"] for y in years}
    assert (f"B2 curtails {curtailed[2024]:,.0f} MWh in 2024 and {curtailed[2025]:,.0f} MWh in "
            f"2025 at the default plant") in text
    exact, blended = {}, {}
    for y in years:
        b0 = recent[y].loc[B0]
        none = negprice[y][negprice[y]["vintage"].str.contains("recent")
                           & (negprice[y]["rule"] == "none")].set_index("rung").loc[B0]
        exact[y] = (none["full_export_premium_by_technology_eur"]
                    - b0["full_export_premium_by_technology_eur"])
        blended[y] = none["premium_revenue_eur"] - b0["premium_revenue_eur"]
    assert (f"B0 at the default plant loses {exact[2024]:,.0f} EUR to that rule in 2024 and "
            f"{exact[2025]:,.0f} EUR in 2025; the blended rate, which every table settles on, "
            f"takes {blended[2024]:,.0f} and {blended[2025]:,.0f} EUR") in text


def test_the_prose_quotes_the_settings_the_tables_ran_with():
    """Numbers in the README's prose that describe how a table ran: the wide connection, the
    applicable values, B3's horizon and planning months, the synthetic demo's settings, where
    the connection binds, and what B2 curtails with and without the battery."""
    text, years = prose(), (2024, 2025)
    plant = report(LADDER[0]).set_index("rung").loc[B3]           # the default plant
    rated = (f"{plant['plant_wind_mw']:.0f} MW onshore wind + {plant['plant_pv_mw']:.0f} MWp PV",
             f"a {plant['plant_bess_mw']:.0f} MW / {plant['plant_bess_mwh']:.0f} MWh battery")
    assert f"{rated[0]} + {rated[1]} behind a {plant['plant_conn_mw']:.0f} MW connection" in text
    assert (f"{rated[0]} (a DC rating) + {rated[1]} behind one {plant['plant_conn_mw']:.0f} MW "
            f"connection") in text
    assert plant["plant_eta_c"] == plant["plant_eta_d"] and pd.isna(plant["plant_soc_init_frac"])
    assert (f"Battery: {plant['plant_bess_mw']:.0f} MW / {plant['plant_bess_mwh']:.0f} MWh, used "
            f"between {100 * plant['plant_soc_min_frac']:.0f} % and "
            f"{100 * plant['plant_soc_max_frac']:.0f} % of its energy, "
            f"{100 * plant['plant_eta_c']:.0f} % efficient each way, starting at its minimum, "
            f"with linear wear of {plant['plant_c_deg_eur_mwh']:.0f} EUR per MWh") in text
    assert f"a hard export limit of {plant['plant_conn_mw']:.0f} MW" in text
    assert f"wears at {plant['plant_c_deg_eur_mwh']:.0f} EUR per MWh of throughput" in text
    wide = report("decompose_2024.csv").set_index("scenario").loc["unconstrained connection"]
    assert f"at its connection as built and at {wide['plant_conn_mw'].iloc[0]:.0f} MW" in text

    vintages = report("negprice_2024.csv").groupby("vintage", sort=False).first()
    recent, older = (vintages.loc[[v for v in vintages.index if word in v][0]]
                     for word in ("recent", "older"))
    assert (f"applicable values are {recent['market_anzulegender_wert_wind']:.1f} EUR/MWh for "
            f"wind and {recent['market_anzulegender_wert_pv']:.1f} for PV") in text
    assert (f"The older one, at {older['market_anzulegender_wert_wind']:.1f} and "
            f"{older['market_anzulegender_wert_pv']:.1f} EUR/MWh") in text

    b3 = [report(name).set_index("rung").loc[B3] for name in LADDER]
    hours = {row["b3_horizon_steps"] * row["dt_h"] for row in b3}
    assert len(hours) == 1
    assert f"over the next {hours.pop():.0f} hours and applies the first step" in text
    start = [pd.Timestamp(row["window_start_berlin"]) for row in b3]
    before = [s - pd.DateOffset(months=1) for s in start]
    assert (f"{before[0].month_name()} {before[0].year}'s in the {start[0].month_name()} window "
            f"and {before[1].month_name()} {before[1].year}'s in the {start[1].month_name()} "
            f"window") in text
    assert (f"in the {start[0].month_name()} window B3 plans on {before[0].month_name()} "
            f"{before[0].year}'s premium") in text

    demo = report("b3_ablation_synthetic_demo.csv").iloc[0]
    assert demo["premium_period"] == "fixed"
    assert (f"behind a {demo['plant_conn_mw']:.0f} MW connection, B3 on a "
            f"{demo['b3_horizon_steps'] * demo['dt_h']:.0f}-hour horizon, and one fixed premium "
            f"of {demo['premium_rate_mean_eur_mwh']:.0f} EUR/MWh") in text
    assert (f"over the next {b3[0]['b3_horizon_steps']:.0f} quarter-hours "
            f"({demo['b3_horizon_steps']:.0f} on the synthetic days)") in text
    windows = report("window_stats.csv").set_index("window")
    assert (f"the synthetic days run it behind "
            f"{windows.loc['synthetic_demo', 'connection_mw']:.0f} MW") in text

    peak = {y: windows.loc[str(y), "peak_generation_mw"] for y in years}
    forced = {y: report(f"sizing_{y}.csv").set_index(["rung", "conn_mw"]).loc[B0,
                                                                               "forced_curtail_mwh"]
              for y in years}
    binds = {y: forced[y][forced[y] > 0].index.max() for y in years}
    assert (f"{peak[2024]:.1f} MW in 2024 and {peak[2025]:.1f} MW in 2025: in the sweep, at "
            f"{binds[2024]:.0f} MW and below in 2024 and at {binds[2025]:.0f} MW and below in "
            f"2025") in text
    for y in years:
        assert (forced[y][forced[y].index > binds[y]] == 0).all()

    built = {y: report(f"decompose_{y}.csv").set_index(["scenario", "rung"]).loc["as built"]
             for y in years}
    battery, conn = built[2025].loc[B2, "plant_bess_mw"], built[2025].loc[B2, "plant_conn_mw"]
    assert peak[2025] + battery < conn < peak[2024] + battery
    assert (f"in 2025 the plant's peak generation plus the battery's full {battery:.0f} MW stays "
            f"below {conn:.0f} MW") in text
    kept, without = ({y: built[y].loc[rung, "chosen_curtail_mwh"] for y in years}
                     for rung in (B2, "B2 no battery"))
    assert (f"at {conn:.0f} MW, B2 curtails {kept[2024]:,.0f} MWh in 2024 against "
            f"{without[2024]:,.0f} MWh without the battery, and {kept[2025]:,.0f} against "
            f"{without[2025]:,.0f} MWh in 2025") in text


def test_the_readme_states_how_many_tests_there_are():
    """The README states the suite's size in two places, and pytest's own count of this
    folder's tests keeps both right. It only collects, so no test runs twice."""
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         str(PROJECT / "tests")], cwd=PROJECT, capture_output=True, text=True)
    count = re.search(r"^(\d+) tests? collected", collected.stdout, re.MULTILINE)
    assert count, collected.stdout[-2000:] + collected.stderr[-2000:]
    assert re.findall(r"(\d+) tests\b", readme()) == [count.group(1)] * 2


def _tracked() -> tuple[set[str], str]:
    """Every file git tracks, by its path from the repository's root in git's own case, and
    this project's folder in the same form, e.g. "projects/02-utility-hybrid-plant-dispatch/".
    A link resolves on GitHub only to such a file; the file system here ignores case and also
    holds untracked files."""
    def git(*args: str, cwd: Path = PROJECT) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                              text=True).stdout
    try:
        root = Path(git("rev-parse", "--show-toplevel").strip())
        files = set(git("ls-files", cwd=root).splitlines())
        prefix = git("rev-parse", "--show-prefix").strip()
    except (OSError, subprocess.CalledProcessError) as err:
        pytest.skip(f"git is not available: {err}")
    return files, prefix


def test_every_relative_link_in_the_readme_resolves():
    """A link or figure must name a file git tracks, in its exact case, or a folder that holds
    one; and a link whose text is a path, e.g. [`sizing_2024.csv`](...), must end in it, so it
    cannot point at another year's table."""
    links = re.findall(r"\[([^\]]*)\]\(([^)\s]+)\)", readme())
    local = [(name, target) for name, target in links
             if not target.startswith(("http://", "https://", "#", "mailto:"))]
    assert local
    files, prefix = _tracked()
    missing = []
    for name, target in local:
        path = posixpath.normpath(posixpath.join(prefix, target.split("#")[0]))
        found = (any(f.startswith(f"{path}/") for f in files) if target.endswith("/")
                 else path in files)
        if not found:
            missing.append(target)
        if name.startswith("`") and name.endswith("`"):
            assert target.split("#")[0].endswith(name.strip("`")), f"[{name}]({target})"
    assert not missing, missing

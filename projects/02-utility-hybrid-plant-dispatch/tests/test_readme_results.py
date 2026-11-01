"""The README's results against the committed tables in reports/.

Each result table in the README must hold what its CSV holds, cell by cell, to the precision
it prints; the claims it makes about the tables must hold in them; and its key results may
quote only numbers those tables print. The README's figures once came from console runs that
no file kept, and outlived the code that made them. Offline and fast: this reads files only.
"""
from __future__ import annotations

import re
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
    assert header == ["Rule", f"Plants that carry it in {year}", "Recent: B0 EUR",
                      "Recent: B2 EUR", "Recent: B2 chosen curtailment MWh", "Older: B0 EUR",
                      "Older: B2 EUR", "Older: B2 chosen curtailment MWh"]
    rules = list(dict.fromkeys(d["rule"]))
    carried = {rule: vintage for vintage, rule in vintage_rules(year).items()}
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
    """The scope note's figures: Project 01's SAC, the median of its seeds, and its B3."""
    path = Path("..", "01-prosumer-pv-bess-mpc-rl", "reports", "rl_eval_H28", "rl_summary.csv")
    capture = pd.read_csv(PROJECT / path, index_col=0)["capture of B1->B2 %"]
    sac = capture[capture.index.str.startswith("RL SAC")].median()
    b3 = capture[capture.index.str.startswith("B3")].item()
    text = readme()
    assert f"({path.as_posix()})" in text
    assert f"{sac:.1f} %" in text and f"{b3:.1f} %" in text


def test_every_relative_link_in_the_readme_resolves():
    """A link or figure whose file is gone, or was never committed, fails here."""
    targets = re.findall(r"\]\(([^)\s]+)\)", readme())
    local = [t for t in targets if not t.startswith(("http://", "https://", "#", "mailto:"))]
    assert local
    missing = [t for t in local if not (PROJECT / t.split("#")[0]).exists()]
    assert not missing, missing

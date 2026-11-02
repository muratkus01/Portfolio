"""The README's results against the committed tables in reports/.

Each result table in the README must hold what its CSV holds, cell by cell, to the precision
it prints, and the numbers its prose quotes must be those of the tables, each in the sentence
of its own household, period and controller. The README's numbers were once worked out by
scripts in a gitignored folder, so nothing kept them in step with the CSVs. Where the README
compares with the results from before the HTW clock fix, the tables of that time are read
from git history; a shallow clone skips those checks. Offline: this reads files and asks git.

Not checked here: the solve times and the one-off checks the README marks as such, section 0
(its measured weeks are not in the repository), and the numbers of the two scripts in
scripts/, which need the gitignored H28 dataset.
"""
from __future__ import annotations

import io
import posixpath
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from prosumer.experiments.year_split import periods, run_year_split

PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
LOCAL_TZ = "Europe/Berlin"
B1, B2, B3 = "B1 rule-based", "B2 perfect foresight", "B3 NWP PV, SLP load, fitted TV"
PERFECT, PERFECT_PV = "B3 perfect PV+load, fitted TV", "B3 perfect PV, SLP load, fitted TV"
CAPTURE = "capture of B1->B2 %"
# B0 is the sizing grid's cost plus savings, each rounded to the cent: known to 0.01 EUR/a.
B0_SLACK = 0.01
# The configurations of the README's sizing table, in its order.
SIZING_ROWS = [(5.0, 3.0), (7.5, 4.6), (9.37, 5.63), (12.0, 5.63), (15.0, 5.63), (15.0, 7.5)]
# The ablation table's formulations, by the README's name: the run each one quotes, with its
# action and reward mode.
FORMULATIONS = {
    "Original: clipped action, total-cost reward": ("rl_ablation_clip_net_cost", "clip",
                                                    "net_cost"),
    "Feasible action rescaling only": ("rl_ablation_rescale_net_cost", "rescale", "net_cost"),
    "Differential reward only": ("rl_ablation_clip_differential", "clip", "differential"),
    "Both (reported above)": ("rl_eval_H28", "rescale", "differential"),
}
# The commits that published the reruns after the HTW clock fix (c07847e): the H28 and H31
# evaluations, the RL formulations, and the sweep with the sizing grid. Each one's parent
# holds the tables from before the fix, which the README compares with.
RERUN_HTW, RERUN_RL, RERUN_SWEEP = "0b47c7b", "e8a75ad", "cf141c7"
FIRST_RL = "6fbba1a"            # the first RL result: clipped action, total-cost reward
# Holiday-consistent states as the README names them.
STATE_NAMES = {"BE": "Berlin", "BB": "Brandenburg", "HB": "Bremen", "HH": "Hamburg",
               "MV": "Mecklenburg-Western Pomerania", "NI": "Lower Saxony",
               "SH": "Schleswig-Holstein", "TH": "Thuringia"}
WORDS = {3: "three", 4: "four", 8: "eight"}


def readme() -> str:
    """The README's text, with any Unicode minus sign as an ASCII one."""
    return (PROJECT / "README.md").read_text(encoding="utf-8").replace("−", "-")


def prose() -> str:
    """The README on one line: every run of whitespace one space and bold marks gone, so a
    sentence reads the same wherever its lines wrap."""
    return " ".join(readme().replace("**", "").split())


def report(name: str, **kwargs) -> pd.DataFrame:
    """A committed table of reports/, e.g. report("sizing_grid/sizing_grid.csv")."""
    return pd.read_csv(REPORTS / name, **kwargs)


def summary(folder: str) -> pd.DataFrame:
    """The summary of a rolling evaluation or an RL run, by controller.

    folder: e.g. "rolling_eval_htw_H28" or "rl_ablation_clip_net_cost".
    """
    name = "rl_summary.csv" if folder.startswith("rl_") else "rolling_eval_summary.csv"
    return report(f"{folder}/{name}", index_col=0)


def seed_captures(s: pd.DataFrame) -> pd.Series:
    """Capture of the B1 to B2 gap per SAC seed of an RL summary."""
    return s.loc[s.index.str.startswith("RL SAC seed"), CAPTURE]


def median_seed(s: pd.DataFrame) -> str:
    """The row of the seed whose capture is the median of an odd number of seeds."""
    captures = seed_captures(s)
    assert len(captures) % 2 == 1
    return captures.sort_values().index[len(captures) // 2]


def quarter_hours() -> pd.DatetimeIndex:
    """The quarter-hours of the test period: from 2025-01-01 to the end of the quarter-hour
    study, whose CSV records the days it covers from 2025-10-01."""
    days = report("quarter_hour_study_H28/resolution_summary.csv")["days covered"].unique()
    assert len(days) == 1
    end = pd.Timestamp("2025-10-01") + pd.Timedelta(days=float(days[0]))
    return pd.date_range("2025-01-01", end, freq="15min", tz=LOCAL_TZ, inclusive="left")


def per_year() -> float:
    """Factor from a total over the test period to a year, as the sizing grid annualises."""
    return 8760 / (len(quarter_hours()) * 0.25)


def sizing(controller: str) -> pd.DataFrame:
    """The sizing grid of one controller ("b3" or "b2"), by battery kWh and inverter kW."""
    g = report("sizing_grid/sizing_grid.csv")
    return g[g["controller"] == controller].drop(columns="controller").set_index(
        ["battery_kwh", "inverter_kw"])


def no_battery_per_year() -> float:
    """B0's net cost per year: the same site without a battery. Every row of the sizing grid
    holds its cost and its savings against B0, so their sum is B0 (to B0_SLACK)."""
    g = report("sizing_grid/sizing_grid.csv")
    b0 = g["annual_cost_eur"] + g["annual_savings_eur"]
    assert b0.max() - b0.min() <= 2 * B0_SLACK
    return float(b0.median())


def table(caption: str) -> tuple[list[str], list[list[str]]]:
    """The markdown table below the one README line that holds `caption`.

    caption: e.g. "savings are measured against the same site without a battery".
    Returns (header cells, rows of cells), each cell's text without markup: a link keeps its
    text, and bold and code marks go.
    """
    lines = readme().splitlines()
    at = [i for i, line in enumerate(lines) if caption in line and not line.startswith("|")]
    assert len(at) == 1, f"the README needs one line with {caption!r}, not {len(at)}"
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


def assert_number(text: str, want: float, where: str, slack: float = 0.0) -> None:
    """`text` prints `want`, e.g. "1,970", "+90.56" or "-7.3": within half a unit of its last
    digit, widened by `slack` where `want` comes from numbers a CSV has already rounded."""
    assert re.fullmatch(r"[+-]?\d{1,3}(,\d{3})*(\.\d+)?", text), f"{where}: {text!r}"
    value = float(text.replace(",", ""))
    assert not (value == 0 and text.startswith("-")), f"{where}: {text!r} prints -0"
    unit = 10.0 ** -len(text.partition(".")[2])
    assert abs(value - want) <= unit / 2 + slack + 1e-9 * abs(want), (
        f"{where}: {text!r} does not print {want}")


def assert_row(cells: list[str], expected: list, where: str) -> None:
    """One README row holds `expected`, cell by cell.

    expected: per cell a str, matched exactly, or (number, unit) for the number printed with
    that unit after it, e.g. (89.4296, " %") for "89.4 %", optionally with a slack as third
    item (see assert_number). where: names the row in a failure, e.g. "benchmark, B3".
    """
    assert len(cells) == len(expected), f"{where}: {cells} against {expected}"
    for cell, want in zip(cells, expected):
        if isinstance(want, str):
            assert cell == want, f"{where}: {cell!r}, expected {want!r}"
            continue
        value, unit, *slack = want
        assert cell.endswith(unit), f"{where}: {cell!r} does not end in {unit!r}"
        assert_number(cell[:len(cell) - len(unit)] if unit else cell, value, where, *slack)


def _git(*args: str, cwd: Path = PROJECT) -> str:
    """The output of one git command run in `cwd`; raises if git fails."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def before(rev: str, name: str, **kwargs) -> pd.DataFrame:
    """A table of reports/ as commit `rev` holds it, e.g. before("cf141c7^", "x.csv").

    Skips the test where git is missing, or where a shallow clone lacks the commit. A full
    clone without it fails: the README's comparison would then rest on nothing.
    """
    try:
        shallow = _git("rev-parse", "--is-shallow-repository").strip() == "true"
    except (OSError, subprocess.CalledProcessError) as err:
        pytest.skip(f"git is not available: {err}")
    try:
        text = _git("show", f"{rev}:./{name}", cwd=REPORTS)
    except subprocess.CalledProcessError:
        if shallow:
            pytest.skip(f"this shallow clone lacks commit {rev}")
        raise
    return pd.read_csv(io.StringIO(text), **kwargs)


def test_the_test_period_is_the_one_the_readme_states():
    """625 days from 2025-01-01, 59,996 quarter-hours: hourly products on the first 273 days and
    quarter-hour products on the last 352. Every monthly table covers the same months."""
    steps = quarter_hours()
    switch = pd.Timestamp("2025-10-01", tz=LOCAL_TZ)
    hourly, quarter = steps[steps < switch], steps[steps >= switch]
    days = lambda s: len(set(s.date))
    text = prose()
    assert (f"from {steps[0]:%Y-%m-%d} to {steps[-1]:%Y-%m-%d} ({days(steps)} days, "
            f"{len(steps):,} decision steps at 15-minute resolution)") in text
    assert (f"on the first {days(hourly)} days ({len(hourly):,} steps, until "
            f"{hourly[-1]:%Y-%m-%d}) and quarter-hourly on the last {days(quarter)} "
            f"({len(quarter):,} steps;") in text
    assert f"over the {days(quarter)} days from 2025-10-01 to {steps[-1]:%Y-%m-%d}" in text
    assert f"2026 ends on {steps[-1].day} {steps[-1]:%B}." in text
    months = sorted(set(steps.strftime("%Y-%m")))
    for name in ("rolling_eval_htw_H28/rolling_eval_monthly_cost.csv",
                 "rolling_eval_htw_H31/rolling_eval_monthly_cost.csv",
                 "rl_eval_H28/rl_monthly_cost.csv"):
        assert list(report(name, index_col=0).index) == months, name
    qh = report("quarter_hour_study_H28/resolution_monthly_cost.csv", index_col=0).index
    assert list(qh) == [m for m in months if m >= "2025-10"]


def test_the_benchmark_table_quotes_the_summaries_and_the_sizing_grid():
    """B1 to B3 from the H28 rolling evaluation and RL from its median seed, in total and per
    year; B0 from the sizing grid, which holds every configuration's savings against it.
    Payback and return are on the thesis battery's marginal CAPEX. The solve times are
    one-off measurements, except B3's, whose footnote quotes the CSV."""
    s, rl = summary("rolling_eval_htw_H28"), summary("rl_eval_H28")
    seed = median_seed(rl)
    k = int(seed.split()[-1])
    cycles = report("rl_eval_H28/rl_seeds.csv", index_col="seed")["cycles"]
    f, b0 = per_year(), no_battery_per_year()
    capex = sizing("b3").loc[(9.37, 5.63), "capex_eur"]

    def rung(name: str, label: str, cost: float, capture: tuple, cycles_total: float,
             violations: float, solve: str) -> list:
        """The row of one rung: net cost in total and per year, its share of the gap, cycles
        and violations, the solve time as printed, and payback and return against B0."""
        saved = b0 - cost * f
        return [name, label, (cost, " €"), (cost * f, " €"), capture, (cycles_total * f, ""),
                (violations, ""), solve, (capex / saved, " yr"), (100 * saved / capex, " %")]

    header, rows = table("Full benchmark ladder evaluated on household H28")
    assert header == ["Rung", "Controller", "Net Cost (20.5m)", "Net Cost (€/a)", "Headroom %",
                      "Annual Cycles", "Violations", "Mean Solve", "Payback", "ROCE"]
    rl_label = (f"Overhauled SAC (rescaled action, diff reward), seed {k}, the median of "
                f"{len(seed_captures(rl))} seeds by capture")
    expected = [
        ["B0", "PV only (no battery baseline)", (b0 / f, " €", B0_SLACK / f),
         (b0, " €", B0_SLACK), "-", "0.0", "0", "-", "-", "-"],
        rung("B1", "Rule-based self-consumption", s.loc[B1, "net cost EUR"],
             (s.loc[B1, CAPTURE], " % (B1 ref)"), s.loc[B1, "cycles"], s.loc[B1, "violations"],
             "< 1 ms"),
        rung("B2", "Perfect foresight LP", s.loc[B2, "net cost EUR"], (s.loc[B2, CAPTURE], " %"),
             s.loc[B2, "cycles"], s.loc[B2, "violations"], "4.2 s (yr)"),
        rung("B3", "Deployable rolling MPC (realistic)", s.loc[B3, "net cost EUR"],
             (s.loc[B3, CAPTURE], " %"), s.loc[B3, "cycles"], s.loc[B3, "violations"],
             "5.9 ms ¹"),
        rung("RL", rl_label, rl.loc[seed, "net cost EUR"], (rl.loc[seed, CAPTURE], " %"),
             cycles[k], 0, "1.2 ms"),
    ]
    assert len(rows) == len(expected)
    for cells, want in zip(rows, expected):
        assert_row(cells, want, f"benchmark, {want[0]}")
    assert (report("rl_eval_H28/rl_seeds.csv")["violations"] == 0).all()
    assert f"the rerun CSV shows {s.loc[B3, 'mean_solve_ms']:.1f} ms" in prose()

    # The bold sizing row is the same configuration, run on its own: it must agree.
    for controller, rung_name in (("b3", B3), ("b2", B2)):
        cell = sizing(controller).loc[(9.37, 5.63)]
        assert cell["annual_cost_eur"] == pytest.approx(s.loc[rung_name, "net cost EUR"] * f,
                                                        abs=0.006)
        assert cell["annual_cycles"] == pytest.approx(s.loc[rung_name, "cycles"] * f, abs=0.006)


def test_the_benchmark_bullets_quote_the_same_tables():
    """B3's share of the gap in EUR, its violations, and its economics against B0 and B1."""
    s = summary("rolling_eval_htw_H28")
    b1, b2, b3 = (s.loc[k, "net cost EUR"] for k in (B1, B2, B3))
    g = report("sizing_grid/sizing_grid.csv")
    assert np.allclose(g["capex_eur"], 350 * g["battery_kwh"] + 190 * g["inverter_kw"])
    cell, ceiling = sizing("b3").loc[(9.37, 5.63)], sizing("b2").loc[(9.37, 5.63)]
    saved_b1 = no_battery_per_year() - b1 * per_year()
    text = prose()
    assert (f"captures {s.loc[B3, CAPTURE]:.1f} % of the theoretical perfect-foresight ceiling "
            f"({b1 - b3:.0f} of {b1 - b2:.0f} EUR headroom over B1)") in text
    assert f"Zero constraint violations across all {len(quarter_hours()):,} quarter-hours." in text
    assert (s["violations"] == 0).all()
    assert (f"an annual electricity expense of {no_battery_per_year():+.2f} EUR/a (PV only) into "
            f"a net revenue of {cell['annual_cost_eur']:.2f} EUR/a, an annual gain of "
            f"{cell['annual_savings_eur']:.2f} EUR/a") in text
    assert (f"Against the marginal CAPEX of {cell['capex_eur']:,.0f} EUR (350 EUR/kWh storage "
            f"plus 190 EUR/kW inverter) that is a {cell['simple_payback_years']:.1f}-year simple "
            f"payback and {cell['roce_pct']:.1f} % return on capital, against "
            f"{cell['capex_eur'] / saved_b1:.1f} years and "
            f"{100 * saved_b1 / cell['capex_eur']:.1f} % for the price-blind rule-based "
            f"operation") in text
    assert (f"Perfect foresight would reach {ceiling['simple_payback_years']:.1f} years and "
            f"{ceiling['roce_pct']:.1f} %, so the entire remaining control gap is worth about "
            f"half a point of return") in text
    assert 0.25 <= ceiling["roce_pct"] - cell["roce_pct"] < 0.75


def test_the_capture_ladder_quotes_the_h28_summary():
    """B3's share of the gap, and how the gap splits into horizon, load and PV forecast."""
    s = summary("rolling_eval_htw_H28")
    cap = s[CAPTURE]
    b1, b2, b3 = (s.loc[k, "net cost EUR"] for k in (B1, B2, B3))
    features = report("htw_household_features.csv", index_col=0)
    assert features["distance"].idxmin() == "H28"
    assert len([c for c in features.columns if c != "distance"]) == 5
    text = prose()
    assert (f"For the most typical household (H28, closest to the median of all {len(features)} "
            f"on five load-shape features), the deployable B3 captures {cap[B3]:.1f} % of the "
            f"gain that perfect foresight would achieve over the rule-based controller, "
            f"{b1 - b3:.0f} of {b1 - b2:.0f} EUR over 20.5 months") in text
    assert (f"the price-limited horizon costs about {100 - cap[PERFECT]:.0f} points, the "
            f"household's load not following the standard profile about "
            f"{cap[PERFECT] - cap[PERFECT_PV]:.0f}, and PV forecast error about "
            f"{cap[PERFECT_PV] - cap[B3]:.0f}. Zero constraint violations in every run.") in text
    for folder in ("rolling_eval_htw_H28", "rolling_eval_htw_H31"):
        assert (summary(folder)["violations"] == 0).all(), folder


def test_the_household_sweep_quotes_its_csv():
    """Median, middle half and range over the households that pass the PV screen; H31 from
    its own evaluation, which the sweep's row for it must repeat."""
    sweep = report("household_sweep.csv").set_index("household")
    b3 = sweep["capture_b3_pct"]
    screen = report("htw_pv_screen.csv", index_col=0)
    assert sorted(sweep.index) == sorted(screen.index[~screen["pv_suspect"]])
    assert len(screen) == 74 and screen["pv_suspect"].sum() == 1
    assert (sweep["violations_b3"] == 0).all()
    for household, folder in (("H28", "rolling_eval_htw_H28"), ("H31", "rolling_eval_htw_H31")):
        assert b3[household] == pytest.approx(summary(folder).loc[B3, CAPTURE], abs=1e-3)
    h31 = summary("rolling_eval_htw_H31")[CAPTURE]
    text = prose()
    assert (f"Across all {len(sweep)} measured households the deployable B3 captures a median "
            f"{b3.median():.1f} % (middle half {b3.quantile(0.25):.1f} to "
            f"{b3.quantile(0.75):.1f} %, range {b3.min():.1f} to {b3.max():.1f} %)") in text
    assert (f"H31, likely with an instantaneous electric water heater, reaches "
            f"{h31[PERFECT]:.1f} % with perfect forecasts, {h31[PERFECT_PV]:.1f} % with the "
            f"standard-profile load forecast and {h31[B3]:.1f} % as deployed, with the PV "
            f"forecast as well.") in text


def test_the_clock_fix_comparisons_quote_the_tables_from_before_it():
    """The sweep, B3 on H28 and the sizing row, before and after the fix: each "before"
    figure must be that of the table the rerun replaced."""
    new = report("household_sweep.csv").set_index("household")["capture_b3_pct"]
    old = before(f"{RERUN_SWEEP}^", "household_sweep.csv").set_index("household")[
        "capture_b3_pct"]
    assert sorted(old.index) == sorted(new.index)
    moved = new - old.reindex(new.index)
    text = prose()
    assert (f"The clock fix moved the median by {new.median() - old.median():+.1f} points "
            f"({old.median():.1f} % before), H28 by {moved['H28']:+.1f} and H31 by "
            f"{moved['H31']:+.1f}, and no household by more than {moved.max():+.1f} or "
            f"{moved.min():+.1f}") in text
    h28 = before(f"{RERUN_HTW}^", "rolling_eval_htw_H28/rolling_eval_summary.csv", index_col=0)
    assert (f"(B3 capture {h28.loc[B3, CAPTURE]:.1f} % to "
            f"{summary('rolling_eval_htw_H28').loc[B3, CAPTURE]:.1f} %)") in text
    grid = before(f"{RERUN_SWEEP}^", "sizing_grid/sizing_grid.csv")
    row = grid[(grid["controller"] == "b3") & (grid["battery_kwh"] == 9.37)
               & (grid["inverter_kw"] == 5.63)]["annual_cost_eur"].item()
    assert (f"the sweep median moved {old.median():.1f} % to {new.median():.1f} % and the "
            f"9.37 kWh / 5.63 kW sizing row {row:.1f} to "
            f"{sizing('b3').loc[(9.37, 5.63), 'annual_cost_eur']:.1f} EUR/a") in text


def test_the_rl_before_the_clock_fix_quotes_the_tables_the_rerun_replaced():
    """Both RL changes before the fix, and the one-seed ablations against the first RL
    result, which had the original formulation."""
    both = seed_captures(before(f"{RERUN_RL}^", "rl_eval_H28/rl_summary.csv", index_col=0))
    now = seed_captures(summary("rl_eval_H28"))
    text = prose()
    assert (f"Before the HTW clock fix the same formulation reached {both.median():.1f} % "
            f"({both.min():.1f} % to {both.max():.1f} %); one of those seeds already reached "
            f"today's level") in text
    assert both.max() >= now.min()
    one_seed = {folder: seed_captures(before(f"{RERUN_RL}^", f"{folder}/rl_summary.csv",
                                             index_col=0))
                for folder in ("rl_ablation_rescale_net_cost", "rl_ablation_clip_differential")}
    assert all(len(c) == 1 for c in one_seed.values())
    original = seed_captures(before(FIRST_RL, "rl_eval_H28/rl_summary.csv", index_col=0))
    assert (before(FIRST_RL, "rl_eval_H28/rl_seeds.csv")["clipped_share"] > 0).all()
    assert (f"Before the clock fix each ablation had one seed, and rescaling alone looked "
            f"slightly harmful ({one_seed['rl_ablation_rescale_net_cost'].item():.1f} % against "
            f"{original.median():.1f} %)") in text


def test_the_quarter_hour_study_quotes_its_summaries():
    """The value of the quarter-hour signal for H28 and the standard-profile household, its
    share of the gap, when it is earned, and the error of an hourly model."""
    h28 = report("quarter_hour_study_H28/resolution_summary.csv", index_col="controller")
    slp = report("quarter_hour_study_slp/resolution_summary.csv", index_col="controller")
    value, days = "value of 15-min signal EUR", h28["days covered"].iloc[0]
    text = prose()
    assert (f"worth {h28.loc['B3', value]:.1f} EUR to the deployable B3 over the {days:.0f} days "
            f"from 2025-10-01 to {quarter_hours()[-1]:%Y-%m-%d}, about "
            f"{h28.loc['B3', value + '/yr']:.0f} EUR/a if scaled pro rata to a full year "
            f"({h28.loc['B2', value]:.1f} EUR over the {days:.0f} days with perfect foresight)"
            ) in text
    share = "value as % of B1->B2 headroom"
    gap = h28.loc["B1", "cost 15-min EUR"] - h28.loc["B2", "cost 15-min EUR"]
    for c in ("B2", "B3"):
        assert h28.loc[c, share] == pytest.approx(100 * h28.loc[c, value] / gap, abs=1e-3)
    assert (f"That is {h28.loc['B3', share]:.1f} % of the whole optimisation gain (B1 to B2) "
            f"over the same days ({h28.loc['B2', share]:.1f} % with perfect foresight)") in text
    monthly = report("quarter_hour_study_H28/resolution_monthly_cost.csv", index_col=0)
    by_month = monthly["B3 | hourly signal"] - monthly["B3 | 15-min"]
    march_to_october = pd.PeriodIndex(by_month.index, freq="M").month.isin(range(3, 11))
    assert by_month[march_to_october].sum() > 0.5 * by_month.sum()
    assert "It is earned mostly from March to October." in text
    assert (f"The standard-profile household gives nearly the same values "
            f"({slp.loc['B3', value]:.1f} and {slp.loc['B2', value]:.1f} EUR over the "
            f"{days:.0f} days)") in text
    error = h28["hourly model error EUR"]
    assert error["B1"] < 0 < error["B2"]          # reports B1 better and B2 worse than it is
    assert (f"overstates the rule-based result by {-error['B1']:.1f} EUR, because hourly "
            f"averaging hides the mismatch between PV and load inside the hour, and understates "
            f"the optimised result by {error['B2']:.1f} EUR") in text


def test_the_rl_section_quotes_the_rl_runs():
    """The original formulation's capture and clipping, the reported formulation's capture,
    clipping and violations, and its distance to B1 and to B3."""
    original = seed_captures(summary("rl_ablation_clip_net_cost"))
    clip = 100 * report("rl_ablation_clip_net_cost/rl_seeds.csv")["clipped_share"]
    s, seeds = summary("rl_eval_H28"), report("rl_eval_H28/rl_seeds.csv")
    now, b3 = seed_captures(s), summary("rolling_eval_htw_H28").loc[B3, CAPTURE]
    assert s.loc[B3, CAPTURE] == pytest.approx(b3, abs=1e-3)
    text = prose()
    assert (f"captured a median of {original.median():.1f} % of the B1 to B2 gap "
            f"({original.min():.1f} % to {original.max():+.1f} % across {len(original)} "
            f"seeds)") in text
    clipping = f"{clip.min():.0f} to {clip.max():.0f} %"
    assert f"({clipping} clipping rate)" in text
    assert f"The safety layer clipped {clipping} of actions." in text
    assert f"reducing action clipping from {clipping} down to 0 %." in text
    assert (seeds["clipped_share"] == 0).all() and (seeds["violations"] == 0).all()
    assert (f"dropped from {clip.min():.0f}-{clip.max():.0f} % down to exactly 0.0 % across all "
            f"seeds") in text
    gain = s.loc[B1, "net cost EUR"] - s.loc[median_seed(s), "net cost EUR"]
    assert (f"Headroom recovery swung from {original.median():.1f} % to {now.median():+.1f} % "
            f"(range {now.min():.1f} % to {now.max():.1f} % across seeds), capturing an "
            f"additional {gain:.0f} EUR of economic value over the price-blind B1 heuristic"
            ) in text
    assert (f"Zero constraint violations across all {len(seeds) * len(quarter_hours()):,} "
            f"evaluated quarter-hours") in text
    assert (f"RL remains far behind the deployable MPC ({now.median():.0f} % against "
            f"{b3:.1f} %)") in text


def test_the_ablation_table_quotes_the_four_formulations():
    """Per formulation: the clipping range and the median and range of the seeds' captures,
    each from the run of that formulation; and what the prose reads from them."""
    header, rows = table("The answer is not the one the clipping statistic suggests")
    assert header == ["Formulation", "Clipping",
                      "Capture of the B1 to B2 gap, median (range) of 3 seeds"]
    assert [cells[0] for cells in rows] == list(FORMULATIONS)
    captures = {}
    for (name, clipping, capture), (folder, action, reward) in zip(rows, FORMULATIONS.values()):
        seeds = report(f"{folder}/rl_seeds.csv")
        assert (seeds["action_mode"] == action).all() and (seeds["reward_mode"] == reward).all()
        c = captures[name] = seed_captures(summary(folder))
        assert len(c) == len(seeds) == 3, name
        clip = 100 * seeds["clipped_share"]
        if (clip == 0).all():
            assert clipping == "0 %", name
        else:
            lo, hi = re.fullmatch(r"(\d+) to (\d+) %", clipping).groups()
            assert_number(lo, clip.min(), name)
            assert_number(hi, clip.max(), name)
        printed = re.fullmatch(r"([+-]\d+\.\d) % \(([+-]?\d+\.\d) to ([+-]?\d+\.\d)\)", capture)
        for number, want in zip(printed.groups(), (c.median(), c.min(), c.max())):
            assert_number(number, want, name)
    original, rescale, reward, both = captures.values()
    text = prose()
    assert (f"Removing the uncontrollable part of the cost is worth about "
            f"{reward.median() - original.median():.0f} points on its own") in text
    assert (f"its median is {rescale.median() - original.median():.1f} points above the "
            f"original, but the seed ranges overlap and the best seed of either captures only "
            f"{max(original.max(), rescale.max()):.1f} % of the gap") in text
    assert max(original.min(), rescale.min()) < min(original.max(), rescale.max())
    assert f"adding about {both.median() - reward.median():.0f} further points" in text
    assert both.min() > reward.max()


def test_the_sizing_table_quotes_the_sizing_grid():
    """Per configuration of the table, B3's economics and the B2 ceiling of return; CAPEX is
    350 EUR per kWh plus 190 EUR per kW. The grid has five capacities and four inverters."""
    b3, b2 = sizing("b3"), sizing("b2")
    assert len(b3) == len(b2) == 20
    capacities = sorted(set(b3.index.get_level_values(0)))
    inverters = sorted(set(b3.index.get_level_values(1)))
    assert capacities == [5.0, 7.5, 9.37, 12.0, 15.0] and inverters == [3.0, 4.6, 5.63, 7.5]
    text = prose()
    assert (f"Twenty configurations, five battery capacities ({capacities[0]:.1f} to "
            f"{capacities[-1]:.1f} kWh) against four inverter ratings "
            f"({', '.join(str(x) for x in inverters)} kW)") in text
    assert "Marginal CAPEX is the thesis basis, 350 EUR/kWh of storage plus 190 EUR/kW" in text
    assert "Full sweep across all 20 configurations" in text
    header, rows = table("savings are measured against the same site without a battery")
    assert header == ["Battery", "Inverter", "CAPEX", "Annual net cost (B3)",
                      "Annual savings (B3)", "Cycles/a", "Payback (B3)", "ROCE (B3)",
                      "ROCE ceiling (B2)"]
    assert len(rows) == len(SIZING_ROWS)
    for cells, key in zip(rows, SIZING_ROWS):
        r = b3.loc[key]
        assert_row(cells, [(key[0], " kWh"), (key[1], " kW"), (r["capex_eur"], " €"),
                           (r["annual_cost_eur"], " €"), (r["annual_savings_eur"], " €/a"),
                           (r["annual_cycles"], ""), (r["simple_payback_years"], " yr"),
                           (r["roce_pct"], " %"), (b2.loc[key, "roce_pct"], " %")],
                   f"sizing, {key}")


def test_the_sizing_insights_on_size_and_inverter_quote_the_sizing_grid():
    """Insights 1 and 2: return falls with size at every inverter but once, savings never
    do; and what a larger battery or inverter adds."""
    b3 = sizing("b3")
    roce = b3["roce_pct"]
    exceptions = []
    for kw in sorted(set(b3.index.get_level_values(1))):
        at = b3.xs(kw, level=1)
        assert at["annual_savings_eur"].is_monotonic_increasing, kw
        exceptions += [(kw, kwh) for kwh, step in at["roce_pct"].diff().dropna().items()
                       if step >= 0]
    assert exceptions == [(7.5, 7.5)]
    text = prose()
    assert (f"at 7.5 kW, 7.5 kWh returns slightly more than 5.0 kWh ({roce[(7.5, 7.5)]:.2f} "
            f"against {roce[(5.0, 7.5)]:.2f} %)") in text
    assert (f"5.0 kWh with 3 kW returns {roce[(5.0, 3.0)]:.1f} % on capital, 9.37 kWh with "
            f"5.63 kW {roce[(9.37, 5.63)]:.1f} %, and 15.0 kWh with 5.63 kW "
            f"{roce[(15.0, 5.63)]:.1f} %") in text
    step = b3.loc[(15.0, 5.63)] - b3.loc[(9.37, 5.63)]
    assert (f"Going from 9.37 to 15.0 kWh costs {step['capex_eur']:,.0f} EUR and adds "
            f"{step['annual_savings_eur']:.0f} EUR/a") in text
    cycles = b3["annual_cycles"]
    assert (f"falls from {cycles[SIZING_ROWS[0]]:.0f} to {cycles[SIZING_ROWS[-1]]:.0f} full "
            f"equivalent cycles a year") in text
    step = b3.loc[(9.37, 7.5)] - b3.loc[(9.37, 4.6)]
    assert (f"On a 9.37 kWh battery, going from 4.6 to 7.5 kW adds "
            f"{step['annual_savings_eur']:.1f} EUR/a for {step['capex_eur']:,.0f} EUR of extra "
            f"equipment") in text


def test_the_sizing_insights_on_control_and_the_sweet_spot_quote_the_sizing_grid():
    """Insights 3 and 4: B3 against B2 in every configuration, against the step to a larger
    battery along the table and at a fixed inverter, and the configurations with the highest
    return."""
    b3, b2 = sizing("b3"), sizing("b2")
    roce = b3["roce_pct"]
    ratio = 100 * b3["annual_savings_eur"] / b2["annual_savings_eur"]
    gap = b2["roce_pct"] - roce
    text = prose()
    assert (f"Across all {len(b3)} configurations the deployable B3 realises {ratio.mean():.0f} % "
            f"of the perfect-foresight savings ({ratio.min():.1f} to {ratio.max():.1f} %), a gap "
            f"of {gap.min():.1f} to {gap.max():.1f} points of ROCE") in text
    for a, b in pairwise(SIZING_ROWS):
        if b[0] > a[0]:               # a step down the table to the next larger battery
            assert roce[a] - roce[b] > max(gap[a], gap[b]), (a, b)
    # the same comparison with the inverter held fixed, one capacity step at a time
    inverters = sorted(set(b3.index.get_level_values(1)))
    steps = [((a, kw), (b, kw)) for kw in inverters
             for a, b in pairwise(sorted(b3.xs(kw, level=1).index))]
    fails = [(a, b) for a, b in steps if not roce[a] - roce[b] > max(gap[a], gap[b])]
    top = [(a, b) for a, b in steps if a[1] == inverters[-1]]
    assert all(step in fails for step in top)
    assert (f"With the inverter held fixed, that fails in {len(fails)} of the {len(steps)} steps "
            f"to the next larger battery, including all {WORDS[len(top)]} at {inverters[-1]} kW"
            ) in text
    small = roce[(5.0, 5.63)] - roce[(7.5, 5.63)]
    assert small < gap[(5.0, 5.63)]
    assert (f"with 5.63 kW, going from 5.0 to 7.5 kWh costs only {small:.1f} points, and with "
            f"7.5 kW it raises the return by {roce[(7.5, 7.5)] - roce[(5.0, 7.5)]:.2f} "
            f"points") in text
    sweet = b3.loc[[(5.0, 3.0), (5.0, 4.6), (7.5, 3.0), (7.5, 4.6)]]
    assert set(roce.nlargest(4).index) == set(sweet.index)
    pay, ret = sweet["simple_payback_years"], sweet["roce_pct"]
    assert (f"5 to 7.5 kWh of storage with a 3 to 4.6 kW inverter maximises return "
            f"({pay.min():.1f} to {pay.max():.1f} year payback, {ret.min():.0f} to "
            f"{ret.max():.0f} % ROCE)") in text
    trade = b3.loc[(9.37, 5.63)] - b3.loc[(5.0, 3.0)]
    assert (f"against 5.0 kWh / 3 kW, trades about {-trade['roce_pct']:.0f} points of ROCE for "
            f"{trade['annual_savings_eur']:.0f} EUR/a more absolute savings") in text


def test_the_year_split_table_is_what_the_code_makes_of_the_monthly_costs():
    """`prosumer year-split` on the committed monthly costs reproduces the committed table, so
    the table cannot drift from the code or from its inputs."""
    committed = report("year_split/year_split.csv")
    pd.testing.assert_frame_equal(committed, run_year_split(REPORTS).round(4),
                                  check_dtype=False)


def test_the_year_split_paragraph_quotes_its_table():
    """B3 and the RL seeds over January to September of each year and over 2025, and the
    months in which they cost more than B1."""
    split = report("year_split/year_split.csv").set_index(["period", "controller"])
    cap = split[CAPTURE]
    y25, y26, all25 = ("January to September 2025", "January to September 2026",
                       "January to December 2025")
    gap = {p: split.loc[(p, B1), "net cost EUR"] - split.loc[(p, B2), "net cost EUR"]
           for p in (y25, y26)}
    rl = {p: cap[p][cap[p].index.str.startswith("RL SAC seed")] for p in (y25, y26, all25)}
    assert len(rl[y25]) == len(seed_captures(summary("rl_eval_H28")))
    text = prose()
    assert (f"Over those months the B1 to B2 gap is {gap[y25]:.0f} EUR in 2025 and "
            f"{gap[y26]:.0f} EUR in 2026, and B3 captures {cap[y25, B3]:.1f} % and "
            f"{cap[y26, B3]:.1f} % of it.") in text
    assert (f"The {WORDS[len(rl[y25])]} RL seeds capture {rl[y25].min():.1f} to "
            f"{rl[y25].max():.1f} % in 2025 and {rl[y26].min():.1f} to {rl[y26].max():.1f} % in "
            f"2026, and over the whole of 2025 only {rl[all25].min():.1f} to "
            f"{rl[all25].max():.1f} % (B3: {cap[all25, B3]:.1f} %).") in text

    winter, whole = split.loc["October to February"], split.loc["whole test period"]
    n_winter, n_whole = winter["months"].iloc[0], whole["months"].iloc[0]
    seeds = whole.index.str.startswith("RL SAC seed")
    costlier = "months costlier than B1"
    assert (winter.loc[seeds, costlier] == n_winter).all()
    assert (whole.loc[seeds, costlier] == n_winter).all()        # and in no other month
    assert whole.loc[B3, costlier] == 0
    months = report("rl_eval_H28/rl_monthly_cost.csv", index_col=0).index
    assert list(periods(months)["October to February"]) == [
        "2025-01", "2025-02", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
    assert (f"Every RL seed costs more than B1 in each of the {n_winter} months from October to "
            f"February (January and February 2025, October 2025 to February 2026) and less in "
            f"each of the other {n_whole - n_winter}; B3 costs less than B1 in all {n_whole} "
            f"months.") in text
    assert "([year_split](reports/year_split/year_split.csv))" in text


def test_the_deliverables_quote_the_rerun_solve_time():
    """The mean B3 solve time of the committed H28 rerun, in the list of deliverables."""
    s = summary("rolling_eval_htw_H28")
    assert (f"({s.loc[B3, 'mean_solve_ms']:.1f} ms on average in the committed H28 rerun"
            in prose())


def test_gate_0_and_the_limitations_quote_their_tables():
    """Gate 0's count of published figures and the thesis's optimised profit, from the
    reference CSV; the most winter-heavy household, from the feature table."""
    ref = pd.read_csv(PROJECT / "tests" / "reference" / "thesis_2024_yearly_summary.csv",
                      index_col="scenario")
    text = prose()
    assert len(ref) == 3
    assert f"matches all {ref.shape[1]} published yearly figures of each scenario" in text
    profit = ref.loc["optimized_pv_bess", "Optimization Profit"]
    # only the published profit traces to a file; the corrected one is a marked one-off
    assert re.search(rf"\({profit:.1f} to \d+\.\d EUR, a one-off rerun that no test covers yet\)",
                     text)
    features = report("htw_household_features.csv", index_col=0)["winter_to_summer"]
    assert (f"One home, {features.idxmax()}, uses on average {features.max():.1f} times as much "
            f"power in winter (November to February) as in summer (May to August)") in text


def test_the_aggregate_load_checks_quote_their_reports():
    """The pass/fail count and the table's first four rows: split-half error, temperature
    slope, temperature and the daily shape, and the daily model's rivals."""
    folder, text = "aggregate_load/", prose()
    v = report(folder + "validation_summary.csv")
    assert v["passed"].all()
    assert f"all {len(v)} pass/fail rows pass" in text

    s = report(folder + "split_half.csv")
    assert (f"15-minute nRMSE {s['model_15min'].median():.2f}, against "
            f"{s['h0_15min'].median():.2f} for BDEW H0 at equal energy and "
            f"{s['same_day_15min'].median():.2f} for the fitted half's own measured same-day "
            f"mean") in text
    wins = (s["model_15min"] < s["h0_15min"]) & (s["model_15min"] < s["same_day_15min"])
    per_seed = wins.groupby(s["seed"]).agg(["sum", "size"])
    assert (per_seed["sum"] == per_seed["size"]).all() and per_seed["size"].nunique() == 1
    n = per_seed["size"].iloc[0]
    assert f"better than both in {n} of {n} splits per seed" in text

    a_ref = float(report(folder + "fit_meta.csv", index_col="key").loc["a_ref_kwh", "value"])
    u = report(folder + "slope_uncertainty.csv")
    pick = lambda q, m, seed=None: u[(u["quantity"] == q) & (u["method"] == m) & (
        u["seed"].isna() if seed is None else u["seed"] == seed)]["value"].item()
    beta = -pick("beta", "ols")                              # kWh per K colder
    ci = {seed: (-pick("p97.5", "two_way", seed), -pick("p2.5", "two_way", seed))
          for seed in (0.0, 1.0)}
    assert (f"1 K colder adds {beta:.3f} kWh per household and day at the HTW level of "
            f"{a_ref:,.0f} kWh/a, about {100 * beta / (a_ref / 365):.1f} % at any annual "
            f"energy; two-way bootstrap 95 % CI {ci[0.0][0]:.3f} to {ci[0.0][1]:.3f} kWh "
            f"(seed 0) and {ci[1.0][0]:.3f} to {ci[1.0][1]:.3f} kWh (seed 1)") in text
    naive, honest = pick("se", "naive"), pick("se", "two_way", 0.0)
    assert round(2 * naive / honest) / 2 == 0.5
    assert (f"The naive standard error ({naive:.3f}) understates the honest one "
            f"({honest:.3f}) by half") in text

    hours = report(folder + "shape_temperature.csv")
    top = hours.loc[hours["corr_with_anomaly"].abs().idxmax()]
    shape = report(folder + "shape_model_selection.csv", index_col="variant")["rmse_cv28_kw"]
    assert shape["harmonic_k2_temperature"] > shape["harmonic_k2"]
    assert (f"the largest hourly correlation with the anomaly is {top['corr_with_anomaly']:.2f} "
            f"at {int(top['hour_local']):02d}:00 (that hour's share "
            f"{top['share_change_pct_per_k_colder']:+.1f} % per K colder), and adding temperature "
            f"to the shape does not lower the cross-validated error "
            f"({shape['harmonic_k2_temperature']:.4f} against {shape['harmonic_k2']:.4f} kW)"
            ) in text

    proxy = report(folder + "fit_meta.csv", index_col="key").loc["proxy", "value"]
    d = report(folder + "daily_model_selection.csv")
    d = d[(d["site"] == proxy) & (d["dec_24_31"] == "sunday")]
    cv = lambda n, k, t, weekend=True: d[(d["classes"] == n) & (d["k"] == k) & (
        d["temperature"] == t) & (d["weekend_season"] == weekend)]["cv28"].item()
    assert (f"28-day blocked cross-validation error of daily energy "
            f"{cv(7, 3, 'normal_anomaly'):.3f} kWh, against {cv(7, 3, 'none'):.3f} without "
            f"temperature, {cv(7, 3, 'normal_anomaly', False):.3f} with a constant weekend "
            f"uplift, {cv(3, 3, 'normal_anomaly'):.3f} with three day classes and "
            f"{cv(7, 3, 'anomaly_31d'):.3f} with an anomaly against a 31-day mean") in text


def test_the_aggregate_load_descriptions_quote_their_reports():
    """The table's last four rows: peak-to-mean, the residual, the states, and the
    normal-weather energy; and, above the table, the peak-to-mean of a single home."""
    folder, text = "aggregate_load/", prose()
    meta = report(folder + "fit_meta.csv", index_col="key")["value"]
    homes = report("htw_household_features.csv", index_col=0)["peak_to_mean"]
    # with an odd number of homes the median is one home's own value
    assert len(homes) == int(meta["households"]) and len(homes) % 2 == 1
    assert (f"one measured household (peak-to-mean {homes.median():.1f} at 15 minutes)"
            in text)
    assert f"fitted once to the mean of the {meta['households']} HTW households of 2010" in text
    ins = report(folder + "insample_2010.csv", index_col="source")
    peak = ins["peak_to_mean"]
    assert (f"model {peak['model']:.2f}, measured {meta['households']}-household mean "
            f"{peak['measured_73_mean']:.2f}, H0 {peak['h0']:.2f}") in text

    r = report(folder + "residual_decomposition.csv", index_col="scale")
    q, day = r.loc["15min_kw"], r.loc["daily_kwh"]
    assert (f"{q['total_rms']:.3f} kW RMS at 15 minutes, of which {q['idiosyncratic_rms']:.3f} "
            f"is household noise that averages out in a fleet and {q['common_mode_rms']:.3f} is "
            f"shared by everyone") in text
    assert (f"per day {day['total_rms']:.2f} kWh, {day['idiosyncratic_rms']:.2f} and "
            f"{day['common_mode_rms']:.2f}") in text

    st = report(folder + "state_summary.csv")
    corr = st["corr_daily_with_16_state_mean"]
    assert (f"Daily energy correlates {corr.min():.3f} to {corr.max():.3f} with the 16-state "
            f"mean ({st['year'].min()} to {st['year'].max()})") in text
    largest = pd.to_datetime(st["max_dev_date"])
    repentance = (st["state"] == "SN") & (largest.dt.month == 11) & (
        largest.dt.day.between(16, 22)) & (largest.dt.day_of_week == 2)   # Wednesday 16-22 Nov
    sn = st.loc[repentance, "max_daily_dev_pct"]
    assert len(sn) and (st.loc[~repentance, "max_daily_dev_pct"] < sn.min()).all()
    assert f"(Saxony's Day of Repentance, {sn.min():.0f} to {sn.max():.0f} %)" in text

    anomaly = report(folder + "proxy_sensitivity.csv", index_col="site")["mean_anomaly_2010"]
    year = st[st["year"] == 2024]
    assert (f"{float(meta['a_ref_kwh']):,.0f} kWh/a (2010, a year "
            f"{-anomaly[meta['proxy']]:.1f} K below normal, realised "
            f"{ins.loc['measured_73_mean', 'annual_kwh']:,.0f}). 2024 was "
            f"{year['mean_anomaly_k'].min():.1f} to {year['mean_anomaly_k'].max():.1f} K warm, "
            f"which lowers realised energy by "
            f"{100 * (1 - year['ratio_to_normal_weather'].max()):.1f} to "
            f"{100 * (1 - year['ratio_to_normal_weather'].min()):.1f} %") in text


def test_the_htw_findings_quote_their_reports():
    """The file clock, the regional holidays the homes do not keep, and the proxy site."""
    folder, text = "aggregate_load/", prose()
    c = report(folder + "clock_check.csv").set_index(["transition", "reading"])["step_h"]
    assert (f"({c['spring', 'file_clock']:.2f} h and {c['autumn', 'file_clock']:.2f} h on the "
            f"file clock); under the MEZ reading that `htw.py` used until c07847e it would jump "
            f"by {c['spring', 'mez_reading']:+.2f} h and {c['autumn', 'mez_reading']:+.2f} h"
            ) in text
    power = report(folder + "holiday_detection_power.csv", index_col="share_of_homes_keeping")[
        "detection_rate"]
    assert (f"detected in {100 * power[0.5]:.0f} % of trials when half the homes keep it, "
            f"{100 * power[0.7]:.0f} % at 70 %, {100 * power[0.1]:.0f} % at 10 %") in text
    sites = report(folder + "proxy_sensitivity.csv", index_col="site")
    consistent = list(sites.index[sites["holiday_consistent"]])
    names = [STATE_NAMES[code] for code in consistent]
    assert (f"it is therefore in {', '.join(names[:-1])} or {names[-1]}, not in Saarland"
            in text)
    v = report(folder + "validation_summary.csv")
    spread = v.loc[v["metric"] == "max annual energy range across consistent sites", "value"]
    assert (f"across the {WORDS[len(consistent)]} consistent sites 2024 annual energy moves by "
            f"at most {spread.item():.2f} %") in text


def _tracked() -> tuple[set[str], str]:
    """Every file git tracks, by its path from the repository's root in git's own case, and
    this project's folder in the same form, e.g. "projects/01-prosumer-pv-bess-mpc-rl/"."""
    try:
        root = Path(_git("rev-parse", "--show-toplevel").strip())
        prefix = _git("rev-parse", "--show-prefix").strip()
        # the company PV exports are under NDA: no listing of the repository includes them
        restricted = f":(exclude){prefix}data/raw/thesis_pv"
        files = set(_git("ls-files", "--", ".", restricted, cwd=root).splitlines())
    except (OSError, subprocess.CalledProcessError) as err:
        pytest.skip(f"git is not available: {err}")
    return files, prefix


def test_every_relative_link_in_the_readme_resolves():
    """A link or figure must name a file git tracks, in its exact case, or a folder that holds
    one; and a link whose text is a path, e.g. [`scripts/`](scripts/), must end in it, or lie
    in it where the text names a folder."""
    links = re.findall(r"\[([^\]]*)\]\(([^)\s]+)\)", readme())
    local = [(name, target) for name, target in links
             if not target.startswith(("http://", "https://", "#", "mailto:"))]
    assert local
    files, prefix = _tracked()
    missing = []
    for name, target in local:
        link = target.split("#")[0]
        path = posixpath.normpath(posixpath.join(prefix, link))
        found = (any(f.startswith(f"{path}/") for f in files) if target.endswith("/")
                 else path in files)
        if not found:
            missing.append(target)
        if name.startswith("`") and name.endswith("`"):
            shown = name.strip("`")
            # a folder's name may link a file in it, e.g. its SOURCE.md
            assert link.endswith(shown) or (shown.endswith("/") and link.startswith(shown)), (
                f"[{name}]({target})")
    assert not missing, missing

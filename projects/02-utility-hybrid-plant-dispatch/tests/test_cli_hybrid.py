"""Every CLI command runs end to end, offline, with the data loaders stubbed."""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import hybrid.baselines as baselines
import hybrid.cli as cli
from hybrid.config import NEGATIVE_PRICE_RULES


def fake_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """A synthetic Berlin calendar year, cut to its first `days` days as load_year cuts it.

    The whole year is there because the premium is paid against realised market values of
    whole periods, and B3 plans on the month before its window, December of the year before.
    """
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    idx = pd.date_range(start, end, freq=f"{int(dt * 60)}min", inclusive="left")
    n = len(idx)
    h = np.arange(n) * dt % 24
    pv = 0.7 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    df = pd.DataFrame({
        "wind_cf": np.clip(0.4 + 0.3 * np.sin(np.arange(n) / 60), 0, 1),
        "pv_cf": pv,
        "pv_feed_in_cf": 0.8 * pv,
        "price_eur_mwh": 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 40 * (h == 13),
    }, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


@pytest.mark.parametrize("argv, expect", [
    (["ladder", "--horizon", "16"], "Hybrid plant ladder"),
    (["sizing"], "Connection-capacity sweep"),
    (["negprice"], "Negative-price rule"),
    (["decompose"], "battery's value comes from"),
])
def test_cli_command_runs(monkeypatch, capsys, argv, expect):
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(argv + ["--days", "2"]) == 0
    assert expect in capsys.readouterr().out


def test_sizing_prints_the_plant_and_the_peak_of_the_data_it_loads(monkeypatch, capsys):
    """The sweep's reason, how far the window's peak sits below nameplate, is printed from the
    loaded factors rather than quoted in the code, where it went stale when the data changed.

    Regression test as well: the header read "80 MW wind + MWp PV installed", as if all 80
    were wind.
    """
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["sizing", "--days", "2"]) == 0
    df = fake_year(2025, 2)
    peak = float((50.0 * df["wind_cf"] + 30.0 * df["pv_cf"]).max())
    out = capsys.readouterr().out
    assert "Connection-capacity sweep (50 MW wind + 30 MWp PV installed)" in out
    assert (f"peak combined output in the window: {peak:.1f} MW, {peak / 80.0:.3f} of installed"
            in out)


def test_ladder_names_the_units_and_basis_of_what_it_prints(monkeypatch, capsys):
    """Regression test: the energy above the connection was called unstorable, though the
    battery stores part of it; the curtailment columns had no unit; and the plant's premium
    rate, a blend on the window's own wind/PV mix, read as the plant's rate for the year."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["ladder", "--fast", "--days", "2"]) == 0
    out = capsys.readouterr().out
    assert "unstorable" not in out and "MWh above it" in out
    assert "curt chosen MWh" in out and "curt forced MWh" in out
    assert "2025 (365 days): wind" in out and "plant on this window's wind/PV mix" in out


def window(argv: list[str]) -> tuple:
    """The window a command line evaluates, on fake_year, and its label."""
    args = cli.build_parser().parse_args(argv)
    return cli.load_window(args, 0.25), cli.window_label(args)


def test_a_window_can_start_later_in_the_year(monkeypatch, capsys):
    """Every window used to start on 1 January, so a summer window could not be run. --start
    names the first day in Berlin time, and the window runs on for --days days of 24 h."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    frame, label = window(["ladder", "--year", "2025", "--start", "07-01", "--days", "2"])
    assert frame.index[0] == pd.Timestamp("2025-07-01", tz="Europe/Berlin")
    assert frame.index[-1] == pd.Timestamp("2025-07-02 23:45", tz="Europe/Berlin")
    assert label == "2025-07-01_2d"
    assert cli.main(["ladder", "--fast", "--start", "07-01", "--days", "2"]) == 0
    assert "Hybrid plant ladder, 2 days of 2025 from 07-01" in capsys.readouterr().out


def test_one_day_is_named_in_the_singular(monkeypatch, capsys):
    """Regression test: a one-day window was printed as "1 days of 2025 from 07-01"."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["ladder", "--fast", "--start", "07-01", "--days", "1"]) == 0
    assert "Hybrid plant ladder, 1 day of 2025 from 07-01\n" in capsys.readouterr().out


def test_sizing_prints_a_difference_within_solver_rounding_as_zero(monkeypatch, capsys):
    """Regression test: where B2 no battery equalled B0 up to the solver's rounding, sizing
    printed the value of curtailing at low prices as "-0" at every connection."""
    def ladder(wind, pv, price, run, premium_rate, rebap=None, **kwargs):
        net = {"B0 no battery": 1_000.0, "B1 curtailment avoidance": 1_000.0,
               "B2 perfect foresight": 1_500.0, "B2 no battery": 1_000.0 - 1e-5}
        return {name: {"cost": {"net_revenue": value, "forced_curtail_mwh": 0.0}}
                for name, value in net.items()}

    monkeypatch.setattr(cli, "load_year", fake_year)
    monkeypatch.setattr(cli, "run_ladder", ladder)
    assert cli.main(["sizing", "--days", "1"]) == 0
    values = capsys.readouterr().out.split("(B2 no battery - B0):")[1].split()
    assert "-0" not in values
    assert values.count("500") == 5, "the battery's value, at each connection"


def test_full_year_is_the_berlin_calendar_year(monkeypatch):
    monkeypatch.setattr(cli, "load_year", fake_year)
    frame, label = window(["sizing", "--year", "2024", "--full-year"])
    assert frame.index[0] == pd.Timestamp("2024-01-01", tz="Europe/Berlin")
    assert frame.index[-1] == pd.Timestamp("2024-12-31 23:45", tz="Europe/Berlin")
    assert label == "2024"


@pytest.mark.parametrize("argv", [["--start", "12-31", "--days", "2"], ["--days", "366"],
                                  ["--start", "07-01", "--full-year"]],
                         ids=["runs into next year", "longer than the year", "half a year"])
def test_a_window_outside_its_year_is_refused(monkeypatch, argv):
    """load_year returns one calendar year, so a longer window was cut short without a word."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    with pytest.raises(ValueError, match="2025"):
        window(["ladder", "--year", "2025", *argv])


def test_the_command_refuses_a_window_past_the_end_of_its_year(monkeypatch):
    """Regression test through main() alone, the interface every version has: --days 366 on
    2025 ran the 35,040 steps of 2025's 365 days and called them "366 days of 2025"."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    with pytest.raises(ValueError, match="run past the end of 2025"):
        cli.main(["ladder", "--fast", "--year", "2025", "--days", "366"])


@pytest.mark.parametrize("days", ["-5", "0"])
def test_a_window_shorter_than_a_day_is_refused(monkeypatch, capsys, days):
    """Regression test: --days -5 ran all but the last 5 days of the year, as load_year cuts
    with iloc[:-480], and called them "-5 days"; --days 0 failed later on a KeyError."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    with pytest.raises(SystemExit):
        cli.main(["ladder", "--fast", "--year", "2025", "--days", days])
    assert "at least 1 day" in capsys.readouterr().err


@pytest.mark.parametrize("rule", NEGATIVE_PRICE_RULES)
def test_every_negative_price_rule_can_be_chosen(monkeypatch, capsys, rule):
    """Regression test: --neg-rule offered only none, six_hour and new_2025, so the four-hour
    rule of 2021-2022 and the 2023 vintage's yearly thresholds could not be run."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["ladder", "--fast", "--days", "2", "--neg-rule", rule]) == 0
    assert f"negative-price rule: {rule}" in capsys.readouterr().out


# ------------------------------------------------------------------ afrr
def _load_afrr_fixtures():
    """tests/afrr_fixtures.py, loaded by its path: the root pyproject.toml runs the tests in
    importlib mode, which puts no test folder on sys.path."""
    path = Path(__file__).with_name("afrr_fixtures.py")
    spec = importlib.util.spec_from_file_location("afrr_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


afrr_fixtures = _load_afrr_fixtures()
B0, B1, B2, B3 = ("B0 no battery", "B1 curtailment avoidance", "B2 perfect foresight",
                  "B3 rolling MPC")
JAN_1, JAN_2 = datetime.date(2025, 1, 1), datetime.date(2025, 1, 2)
MARGINAL = afrr_fixtures.GERMAN["marginal"]
# On 2 January the bid of POS 08-12, that product's average of 1 January, is rejected, as its
# marginal lies below it; the bid of NEG 04-08 equals its marginal, which accepts it.
CELLS = ((JAN_2, "POS_08_12", MARGINAL, 0.5),
         (JAN_2, "NEG_04_08", MARGINAL, afrr_fixtures.invented_prices(JAN_1, "NEG_04_08")[0]))
WINDOW_PRICES = ["afrr_up_price_mean_eur_mw_h", "afrr_down_price_mean_eur_mw_h",
                 "afrr_up_price_median_eur_mw_h", "afrr_down_price_median_eur_mw_h",
                 "afrr_up_bid_accepted_share", "afrr_down_bid_accepted_share"]


def fake_capacity(year: int, cache_dir=None):
    """Stand-in for afrr_data.load_capacity_results: January of `year` at invented prices,
    with the rejected and the tied bid of CELLS."""
    return afrr_fixtures.capacity_results(year, CELLS)


def recording_b3(calls: list):
    """A stand-in for B3, so that nothing but B2 is solved: B2's dispatch on B3's prices. With
    aFRR it holds half of what that dispatch reserves, as if it had bid twice what it was
    awarded, and reports two gates, of 0.5 and 1.5 s, one of them failed, and two re-plans
    failed. It keeps B2's rounding bound, which is B2's alone to report. Each call appends to
    `calls` whether it had aFRR, and the forecasts and premium rate it was handed."""
    def b3(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, export_cap=None,
           progress=False, premium=None, afrr=None):
        calls.append((afrr is not None, wind_fc, pv_fc, price_fc, premium[0]))
        res = baselines.b2_perfect(wind, pv, eff_price, run, export_cap, afrr=afrr)
        res["solve_time_s"] = np.array([0.25])
        if afrr is not None:
            res.update(afrr_bid_up=res["afrr_up"], afrr_bid_down=res["afrr_down"],
                       afrr_up=res["afrr_up"] / 2, afrr_down=res["afrr_down"] / 2,
                       afrr_commit_solve_time_s=np.array([0.5, 1.5]), afrr_commit_failures=1,
                       afrr_replan_failures=2)
        return res
    return b3


def afrr_table(monkeypatch, tmp_path, argv: list[str],
               calls: list | None = None) -> pd.DataFrame:
    """Run the afrr command on fake_year and fake_capacity, with B3 stood in for
    (`recording_b3`, recording into `calls`), and read back the CSV it wrote."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    monkeypatch.setattr(cli, "load_capacity_results", fake_capacity)
    monkeypatch.setattr(baselines, "b3_rolling", recording_b3([] if calls is None else calls))
    out = tmp_path / "afrr.csv"
    assert cli.main(["afrr", *argv, "--out", str(out)]) == 0
    return pd.read_csv(out)


@pytest.mark.parametrize("fast", [False, True], ids=["with B3", "fast"])
def test_cli_afrr_writes_the_ablation_table(monkeypatch, tmp_path, capsys, fast):
    """Feature test: no command ran aFRR, so its value had no table. afrr writes one row per
    connection, mode and rung: the ladder without aFRR and with it at 20, 30 and 60 MW, with
    B3 where it ran, on the same forecasts and premium in both, each rung's aFRR value and
    what it reserved, and the window's capacity prices as aggregates only, one value for the
    whole window."""
    calls = []
    df = afrr_table(monkeypatch, tmp_path, ["--days", "2", *(["--fast"] if fast else [])],
                    calls)

    assert [with_afrr for with_afrr, *_ in calls] == ([] if fast else [False, True] * 3)
    for without, with_afrr in zip(calls[::2], calls[1::2]):
        assert all(np.array_equal(a, b) for a, b in zip(without[1:], with_afrr[1:])), \
            "B3 plans on the same forecasts and premium without aFRR and with it"
    rungs = [B0, B1, B2] + ([] if fast else [B3])
    assert list(df[["conn_mw", "afrr", "rung"]].itertuples(index=False, name=None)) == [
        (conn, mode, rung) for conn in (20.0, 30.0, 60.0) for mode in ("off", "on")
        for rung in rungs]
    assert ((df["afrr_price_basis"] == "bid") & (df["afrr_energy_hours_up"] == 4.0)).all()
    off = df[df["afrr"] == "off"]
    assert off[["afrr_capacity_revenue_eur", "afrr_up_mw_h", "market_afrr_enabled"]].isna(
        ).all().all(), "the rows without aFRR carry none of its columns"
    assert_afrr_values(df, fast)
    assert_afrr_reservations(df[df["afrr"] == "on"].set_index(["conn_mw", "rung"]), fast)
    assert_window_prices(df)
    out = capsys.readouterr().out
    assert "aFRR at 20 MW (overbuild 4.00x)" in out and "aFRR at 60 MW" in out
    assert ("B3's share of B2's aFRR value" in out) is not fast


def assert_afrr_values(df: pd.DataFrame, fast: bool) -> None:
    """Each connection's aFRR values are its rungs' net revenue with aFRR less without, on
    every row of that connection, and B3's share is its value over B2's."""
    assert ("afrr_value_b3_eur" in df) is not fast
    for conn, g in df.groupby("conn_mw"):
        net = g.set_index(["afrr", "rung"])["net_revenue_eur"]
        b2 = net["on", B2] - net["off", B2]
        assert b2 > 1.0, f"{conn} MW: capacity is paid on the second day"
        assert g["afrr_value_b2_eur"].to_numpy() == pytest.approx(np.full(len(g), b2))
        if not fast:
            b3 = net["on", B3] - net["off", B3]
            assert g["afrr_value_b3_eur"].to_numpy() == pytest.approx(np.full(len(g), b3))
            assert g["b3_share_of_afrr_value"].to_numpy() == pytest.approx(
                np.full(len(g), b3 / b2))


def assert_afrr_reservations(on: pd.DataFrame, fast: bool) -> None:
    """What each rung reserved, on the rows with aFRR (indexed by connection and rung): the
    MW-hours held are the mean MW held over the window's 48 hours; B0 and B1 hold none; B2's
    row alone has its rounding bound; and B3's row alone its bids and gates, the stand-in's
    bids being twice what it holds."""
    for name in ("up", "down"):
        assert on[f"afrr_{name}_mw_h"].to_numpy() == pytest.approx(
            48 * on[f"afrr_{name}_mean_mw"].to_numpy())
    rung = on.index.get_level_values("rung")
    assert (on.loc[rung.isin([B0, B1]), ["afrr_up_mw_h", "afrr_down_mw_h"]] == 0).all().all()
    assert (on.loc[rung == B2, "afrr_down_mw_h"] > 0).all()
    assert on.loc[rung == B2, "afrr_rounding_bound_eur"].notna().all()
    assert on.loc[rung != B2, "afrr_rounding_bound_eur"].isna().all(), "B3's carries B2's"
    if fast:
        assert "afrr_bid_up_mw_h" not in on
        return
    b3 = on[rung == B3]
    for name in ("up", "down"):
        assert b3[f"afrr_bid_{name}_mw_h"].to_numpy() == pytest.approx(
            2 * b3[f"afrr_{name}_mw_h"].to_numpy())
    gates = ["afrr_committed_days", "afrr_commit_failures", "afrr_replan_failures",
             "afrr_commit_solve_time_mean_s", "afrr_commit_solve_time_max_s"]
    assert (b3[gates] == [2, 1, 2, 1.0, 1.5]).all().all()
    assert on.loc[rung != B3, ["afrr_bid_up_mw_h", *gates]].isna().all().all()


def assert_window_prices(df: pd.DataFrame) -> None:
    """The table holds no product's own capacity price, only one value per window of each
    aggregate: per direction the mean and median of the realised German average over the
    window's 12 products, and how often the bid rule is accepted over the 6 products after
    the first day."""
    assert {column for column in df.columns if "afrr" in column and "price" in column} == {
        "afrr_price_basis", "market_afrr_price_basis", *WINDOW_PRICES[:4]}
    assert (df[WINDOW_PRICES].nunique() == 1).all(), "one value for the whole window"
    row = df.iloc[0]
    for name, prefix in (("up", "POS"), ("down", "NEG")):
        average = [afrr_fixtures.invented_prices(day, f"{prefix}_{h:02d}_{h + 4:02d}")[0]
                   for day in (JAN_1, JAN_2) for h in range(0, 24, 4)]
        assert row[f"afrr_{name}_price_mean_eur_mw_h"] == pytest.approx(np.mean(average))
        assert row[f"afrr_{name}_price_median_eur_mw_h"] == pytest.approx(np.median(average))
    assert (row["afrr_up_bid_accepted_share"], row["afrr_down_bid_accepted_share"]) == (
        pytest.approx(5 / 6), 1.0), "POS 08-12 rejected, NEG 04-08 tied and accepted"


def test_the_afrr_rows_without_afrr_are_those_of_ladder_and_sizing(monkeypatch, tmp_path):
    """The rows without aFRR must be today's results, column for column: at 60 MW those that
    ladder writes, and at 20, 30 and 60 MW those of sizing. aFRR's value is measured against
    them, and they must reproduce the published tables."""
    tables = {"afrr": afrr_table(monkeypatch, tmp_path, ["--days", "1", "--fast"])}
    for name, argv in (("ladder", ["ladder", "--fast"]), ("sizing", ["sizing"])):
        path = tmp_path / f"{name}.csv"
        assert cli.main([*argv, "--days", "1", "--out", str(path)]) == 0
        tables[name] = pd.read_csv(path)
    off = tables["afrr"][tables["afrr"]["afrr"] == "off"]
    for conn, other in ((60.0, tables["ladder"]),
                        *((conn, tables["sizing"]) for conn in (20.0, 30.0, 60.0))):
        mine = off[off["conn_mw"] == conn].set_index("rung")
        theirs = other if "conn_mw" not in other else other[other["conn_mw"] == conn]
        theirs = theirs.set_index("rung").loc[mine.index]
        shared = [column for column in theirs.columns if column in mine.columns]
        assert len(shared) > 40, "every settlement term, the window and the settings"
        pd.testing.assert_frame_equal(mine[shared], theirs[shared], check_exact=True)


def test_the_afrr_b3_without_afrr_is_ladders_b3(monkeypatch, tmp_path):
    """The test above runs --fast, so it checks B0 to B2 only, and B3's setup in the afrr
    command was pinned by no test: planning B3 on the realised premium, sizing its price error
    on the window's own prices, or running it on another seed passed every one. At 60 MW, the
    run without aFRR must hand B3 what ladder hands it, and its rows, B3's included, must be
    the rows ladder writes, column for column."""
    calls, ladder_calls = [], []
    df = afrr_table(monkeypatch, tmp_path, ["--days", "2"], calls)
    monkeypatch.setattr(baselines, "b3_rolling", recording_b3(ladder_calls))
    path = tmp_path / "ladder.csv"
    assert cli.main(["ladder", "--days", "2", "--out", str(path)]) == 0

    # B3 ran without aFRR and with it at 20, 30 and 60 MW, in that order
    without_afrr_at_60 = calls[4]
    assert not without_afrr_at_60[0] and len(ladder_calls) == 1
    assert all(np.array_equal(a, b)
               for a, b in zip(without_afrr_at_60[1:], ladder_calls[0][1:], strict=True)), \
        "B3 plans on ladder's forecasts and premium"
    mine = df[(df["afrr"] == "off") & (df["conn_mw"] == 60.0)].set_index("rung")
    theirs = pd.read_csv(path).set_index("rung").loc[mine.index]
    assert list(mine.index) == [B0, B1, B2, B3]
    assert set(theirs.columns) <= set(mine.columns), "every column ladder writes"
    assert {"b3_premium_rate_mean_eur_mwh", "b3_forecast_price_scale", "b3_seed"} <= set(
        theirs.columns)
    pd.testing.assert_frame_equal(mine[theirs.columns], theirs, check_exact=True)


@pytest.mark.parametrize("days, shares", [
    ("2", "in 83.3% of the up and 100.0% of the down products after the first day"),
    ("1", "in no product: the window has no day after the first"),
], ids=["two days", "one day"])
def test_the_afrr_printout_gives_the_accepted_share_to_the_bid_rule(monkeypatch, tmp_path,
                                                                     capsys, days, shares):
    """Regression test: the window's accepted share was printed as that of "B3's bids", also
    with --fast, where B3 never runs. It counts every product after the window's first day,
    whatever MW B3 offered there, so it is how often the bid rule is accepted: a bid at the
    same product's average of the day before, at or below the realised marginal."""
    afrr_table(monkeypatch, tmp_path, ["--days", days, "--fast"])
    out = capsys.readouterr().out
    assert "B3's bids" not in out
    assert ("the bid rule, each product's average of the day before, is accepted (at or below "
            f"the realised marginal) {shares}\n") in out


def test_b3s_share_is_empty_where_b2_gains_nothing_from_afrr(monkeypatch, tmp_path):
    """On a window of one day no product is bid for, so on "bid" no reservation is paid and
    B2 gains from aFRR at most the solver's rounding, here 0.01 EUR. B3's share of that would
    be noise, so it is left empty, as decompose leaves its share where the battery's value
    lies within the ladder's tolerance."""
    def b2_a_cent_higher_with_afrr(*args, **kwargs):
        res = baselines.run_ladder(*args, **kwargs)
        if kwargs.get("afrr") is not None:
            res[B2]["cost"]["net_revenue"] += 0.01
        return res

    monkeypatch.setattr(cli, "run_ladder", b2_a_cent_higher_with_afrr)
    df = afrr_table(monkeypatch, tmp_path, ["--days", "1"])

    assert df["afrr_value_b2_eur"].to_numpy() == pytest.approx(np.full(len(df), 0.01))
    assert (df["afrr_value_b3_eur"] == 0.0).all()
    assert df["b3_share_of_afrr_value"].isna().all()
    assert df[["afrr_up_bid_accepted_share", "afrr_down_bid_accepted_share"]].isna().all().all()


@pytest.mark.parametrize("argv, noted", [
    (["--horizon", "8"], True), ([], False), (["--horizon", "8", "--fast"], False),
], ids=["B3 on 2 hours", "B3 on the default day", "no B3"])
def test_the_afrr_command_notes_a_re_plan_horizon_shorter_than_a_day(monkeypatch, tmp_path,
                                                                     capsys, argv, noted):
    """Feature test: the afrr command ran B3 on any --horizon without a word, though a re-plan
    must store a reserved product's energy before the product starts, and one that sees less
    than a day ahead can see it too late (test_afrr_b3.py: plans of 4 hours miss an award that
    plans of 24 hours hold). It now says so where B3 runs on less than a day, and only there."""
    afrr_table(monkeypatch, tmp_path, ["--days", "1", *argv])
    out = capsys.readouterr().out
    assert ("note: B3 re-plans 2 h ahead, less than a day" in out) is noted


@pytest.mark.parametrize("argv, error, message", [
    (["--up-hours", "0.5"], SystemExit, "at least 1 hour of its MW"),
    (["--basis", "median"], SystemExit, "invalid choice: 'median'"),
    (["--basis", "average"], ValueError, "--basis average needs --fast"),
    (["--start", "03-15", "--days", "30", "--fast"], ValueError, "whole Berlin days"),
], ids=["band below 60 minutes", "unknown basis", "B3 on the average", "across a clock change"])
def test_the_afrr_command_refuses_what_it_cannot_run_before_solving(monkeypatch, capsys, argv,
                                                                   error, message):
    """Each is refused before the first ladder runs, naming its cause: a positive band shorter
    than the 60 minutes of PQ conditions § 2.7.1, which MarketConfig would refuse with a
    traceback; a basis the model does not know; B3 on the realised average, which it cannot
    bid; and 30 days of 24 h across the clock change of 30 March, which end at 01:00 Berlin,
    while B3 bids for whole Berlin days."""
    def no_ladder(*args, **kwargs):
        raise AssertionError("a ladder ran before the refusal")

    monkeypatch.setattr(cli, "load_year", fake_year)
    monkeypatch.setattr(cli, "load_capacity_results", fake_capacity)
    monkeypatch.setattr(cli, "run_ladder", no_ladder)
    with pytest.raises(error) as refused:
        cli.main(["afrr", *argv])
    assert message in (capsys.readouterr().err if error is SystemExit else str(refused.value))


def test_the_afrr_command_stops_where_b2_earns_less_with_afrr(monkeypatch, tmp_path):
    """B2's LP with aFRR keeps its plan without aFRR open, so B2 cannot earn less with aFRR,
    and a run where it does has a bug: it stops and writes no table. Here the ladder with
    aFRR reports B2 1 EUR below its revenue, on a day that pays no capacity."""
    def b2_lower_with_afrr(*args, **kwargs):
        res = baselines.run_ladder(*args, **kwargs)
        if kwargs.get("afrr") is not None:
            res[B2]["cost"]["net_revenue"] -= 1.0
        return res

    monkeypatch.setattr(cli, "load_year", fake_year)
    monkeypatch.setattr(cli, "load_capacity_results", fake_capacity)
    monkeypatch.setattr(cli, "run_ladder", b2_lower_with_afrr)
    out = tmp_path / "afrr.csv"
    with pytest.raises(AssertionError, match="B2 earns .* EUR with aFRR and .* without"):
        cli.main(["afrr", "--days", "1", "--fast", "--out", str(out)])
    assert not out.exists()

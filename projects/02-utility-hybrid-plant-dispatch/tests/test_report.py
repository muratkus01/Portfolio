"""The report: the publish set's tables written to reports/, with window_stats.csv and
run_meta.csv beside them, and what run_meta records.

Offline and small: the data loader, the capacity lookup, B3 and the synthetic demo are
replaced by stand-ins, and the pinned set by one tiny experiment of each kind.
"""
from __future__ import annotations

import hashlib
import re
import sys

import numpy as np
import pandas as pd
import pytest

import hybrid.baselines as baselines
import hybrid.cli as cli
from hybrid import data
from hybrid.config import PlantConfig, RunConfig

# One tiny experiment of each kind, and the names the report gives them.
TINY = ("sizing --year 2025 --days 1", "negprice --year 2025 --days 1",
        "decompose --year 2025 --days 1", "ladder --year 2025 --days 1 --horizon 4",
        "b3-ablation --synthetic-demo")
NAMES = ["sizing_2025-01-01_1d", "negprice_2025-01-01_1d", "decompose_2025-01-01_1d",
         "ladder_2025-01-01_1d_horizon-4", "b3_ablation_synthetic_demo"]
WINDOW_COLUMNS = ["window", "inputs", "year", "window_start_utc", "window_end_utc",
                  "window_start_berlin", "window_end_berlin", "days", "steps", "dt_h",
                  "mean_price_eur_mwh", "negative_price_quarter_hours",
                  "below_fee_quarter_hours", "fee_eur_mwh", "installed_mw",
                  "peak_generation_mw", "max_combined_cf", "connection_mw",
                  "steps_over_connection", "connection_binds"]


def calendar_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """A synthetic Berlin calendar year, cut to its first `days` days as load_year cuts it;
    its price dips below zero for the quarter-hour from 13:00 every day."""
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    idx = pd.date_range(start, end, freq=f"{int(dt * 60)}min", inclusive="left")
    h = np.arange(len(idx)) * dt % 24
    pv = 0.7 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    df = pd.DataFrame({"wind_cf": 0.5 + 0.4 * np.sin(np.arange(len(idx)) / 30), "pv_cf": pv,
                       "pv_feed_in_cf": 0.8 * pv,
                       "price_eur_mwh": 60 + 40 * np.sin(2 * np.pi * (h - 18) / 24)
                       - 120 * (h == 13)}, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


def capacity(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Stand-in for data.monthly_capacity: 60 GW onshore and 90 GW DC in every month."""
    months = sorted(set(index.tz_convert("Europe/Berlin").strftime("%Y-%m")))
    return pd.DataFrame({"wind_mw": 60_000.0, "pv_mw": 90_000.0}, index=months)


def timed_b2(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, export_cap=None,
             progress=False, premium=None):
    """Stand-in for B3: B2's dispatch on B3's prices, with two solve times."""
    res = baselines.b2_perfect(wind, pv, eff_price, run, export_cap)
    return {**res, "solve_time_s": np.array([0.25, 0.75])}


def tiny_demo():
    """Stand-in for synthetic.demo_inputs: 16 quarter-hours behind a 20 MW connection."""
    from hybrid import synthetic

    run = RunConfig(dt=0.25, horizon_steps=2, plant=PlantConfig(conn_mw=20.0))
    wind = np.r_[np.full(8, 30.0), np.full(8, 10.0)]
    return synthetic.DemoInputs(wind, np.zeros(16), np.r_[np.full(8, 20.0), np.full(8, 90.0)],
                                run, 8.0)


def install_stand_ins(mp: pytest.MonkeyPatch):
    """Replace everything that would touch the network or solve B3, and the pinned set by
    TINY. Returns the report module."""
    from hybrid import report, synthetic

    mp.setattr(cli, "load_year", calendar_year)
    mp.setattr(baselines, "b3_rolling", timed_b2)
    mp.setattr(synthetic, "demo_inputs", tiny_demo)
    mp.setattr(data, "monthly_capacity", capacity)
    mp.setattr(report, "PUBLISH_SET", TINY)
    return report


@pytest.fixture
def stand_ins(monkeypatch):
    return install_stand_ins(monkeypatch)


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    """The tiny set, run once through `report`: the folder it wrote."""
    out = tmp_path_factory.mktemp("reports")
    with pytest.MonkeyPatch.context() as mp:
        install_stand_ins(mp)
        assert cli.main(["report", "--reports-dir", str(out)]) == 0
    return out


def meta(folder) -> pd.DataFrame:
    """run_meta.csv, read as text."""
    return pd.read_csv(folder / "run_meta.csv", dtype=str, keep_default_na=False)


def sha256_of(frame: pd.DataFrame) -> str:
    """The hash run_meta documents: the index as int64 (nanoseconds for timestamps), then per
    column its name and its float64 values."""
    index = frame.index
    stamps = index.as_unit("ns").asi8 if isinstance(index, pd.DatetimeIndex) else index
    digest = hashlib.sha256(np.asarray(stamps, dtype="<i8").tobytes())
    for name in frame.columns:
        digest.update(str(name).encode())
        digest.update(np.asarray(frame[name], dtype="<f8").tobytes())
    return digest.hexdigest()


def test_the_report_writes_every_table_with_its_window_stats_and_metadata(written):
    """Feature test: the README's numbers came from console runs that no file kept."""
    for name in NAMES:
        table = pd.read_csv(written / f"{name}.csv")
        assert {"rung", "net_revenue_eur", "window_start_utc", "plant_conn_mw"} <= set(
            table.columns), name
    stats = pd.read_csv(written / "window_stats.csv")
    assert list(stats.columns) == WINDOW_COLUMNS
    assert sorted(stats["window"]) == ["2025-01-01_1d", "synthetic_demo"], "one row per window"
    day = stats.set_index("window").loc["2025-01-01_1d"]
    assert (day["negative_price_quarter_hours"], day["below_fee_quarter_hours"]) == (1, 1)
    assert (day["connection_mw"], day["installed_mw"]) == (60.0, 80.0)
    assert list(meta(written).columns) == ["experiment", "section", "item", "value"]
    sections = meta(written).groupby("experiment")["section"].unique().map(set)
    assert sorted(sections.index) == sorted(NAMES)
    on_data = {"run", "code", "environment", "input_sha256", "source_url", "capacity_mw",
               "market_value_proxy_eur_mwh"}
    assert all(sections[name] == on_data for name in NAMES[:4])
    assert sections["b3_ablation_synthetic_demo"] == {"run", "code", "environment",
                                                      "input_sha256"}


def test_run_meta_hashes_the_frames_each_experiment_loaded(written):
    """Each input frame's SHA-256 identifies the data a table came from, so that a rerun can
    tell a revision of Energy-Charts' data from a change of code."""
    rows = meta(written)
    hashes = rows[rows["section"] == "input_sha256"]
    loaded = {name: set(g["item"]) for name, g in hashes.groupby("experiment")}
    window, year = "load_year(2025, days=1, dt=0.25)", "load_year(2025, days=None, dt=0.25)"
    assert loaded["sizing_2025-01-01_1d"] == {window, year}
    assert loaded["ladder_2025-01-01_1d_horizon-4"] == {window, year,
                                              "load_year(2024, days=None, dt=0.25)"}
    assert loaded["b3_ablation_synthetic_demo"] == {"synthetic.demo_inputs()"}
    for item, value in zip(hashes["item"], hashes["value"]):
        match = re.fullmatch(r"load_year\((\d+), days=(\w+), dt=([\d.]+)\)", item)
        if match:
            y, d, dt = match.groups()
            frame = calendar_year(int(y), None if d == "None" else int(d), float(dt))
        else:
            demo = tiny_demo()
            frame = pd.DataFrame({"wind_mw": demo.wind, "pv_mw": demo.pv,
                                  "price_eur_mwh": demo.price})
        assert value == sha256_of(frame), item


def test_run_meta_records_the_code_environment_and_sources(written):
    import pulp

    rows = meta(written)
    value = rows[rows["experiment"] == "ladder_2025-01-01_1d_horizon-4"].set_index(
        ["section", "item"])["value"]
    assert re.fullmatch(r"[0-9a-f]{40}|unknown", value["code", "git_commit"])
    assert value["code", "git_dirty"] in {"True", "False", "unknown"}
    assert value["run", "command"] == "ladder --year 2025 --days 1 --horizon 4"
    assert value["run", "argv"].startswith("report --reports-dir")
    assert float(value["run", "wall_time_s"]) >= 0
    version = sys.version_info
    assert value["environment", "python"] == f"{version.major}.{version.minor}.{version.micro}"
    assert (value["environment", "numpy"], value["environment", "pandas"],
            value["environment", "pulp"]) == (np.__version__, pd.__version__, pulp.__version__)
    assert "cbc" in value["environment", "cbc"].lower()
    for year in (2024, 2025):
        for dataset, url in data.source_urls(year).items():
            assert value["source_url", f"{year} {dataset}"] == url
    assert value["capacity_mw", "2025-01 wind_onshore"] == "60000.0"
    assert value["capacity_mw", "2024-12 solar_dc"] == "90000.0"
    year_2025 = calendar_year(2025)
    assert float(value["market_value_proxy_eur_mwh", "2025 wind"]) == pytest.approx(
        np.average(year_2025["price_eur_mwh"], weights=year_2025["wind_cf"]))
    assert ("market_value_proxy_eur_mwh", "2024-12 pv") in value.index


def test_parts_run_apart_add_up_to_one_run_meta(stand_ins, tmp_path):
    """Experiments run in separate processes, one after another or side by side, must add up
    to one run_meta.csv and one window_stats.csv, and a rerun must replace its own rows."""
    only = ["report", "--reports-dir", str(tmp_path), "--only"]
    assert cli.main([*only, "decompose_2025-01-01_1d"]) == 0
    assert cli.main([*only, "ladder_2025-01-01_1d_horizon-4"]) == 0
    both = meta(tmp_path)
    assert cli.main([*only, "decompose_2025-01-01_1d"]) == 0
    rerun = meta(tmp_path)
    assert sorted(rerun["experiment"].unique()) == ["decompose_2025-01-01_1d",
                                                    "ladder_2025-01-01_1d_horizon-4"]
    assert len(rerun) == len(both), "the rerun replaced its own rows"
    assert list(pd.read_csv(tmp_path / "window_stats.csv")["window"]) == ["2025-01-01_1d"]


def test_an_unknown_experiment_is_refused(stand_ins, tmp_path):
    with pytest.raises(ValueError, match="sizing_2023"):
        cli.main(["report", "--reports-dir", str(tmp_path), "--only", "sizing_2023"])


def test_the_report_runs_the_set_a_file_holds(stand_ins, tmp_path):
    """Feature test: the report ran PUBLISH_SET or a part of it, so any other set of
    experiments needed a code edit. --commands names a file of hybrid.cli command lines."""
    commands = tmp_path / "set.txt"
    commands.write_text("# two tiny experiments\nladder --year 2025 --days 1 --horizon 4 --fast\n"
                        "\ndecompose --year 2025 --days 1   # at the default plant\n",
                        encoding="utf-8")
    out = tmp_path / "reports"
    assert cli.main(["report", "--commands", str(commands), "--reports-dir", str(out)]) == 0
    assert sorted(path.name for path in out.glob("*.csv")) == [
        "decompose_2025-01-01_1d.csv", "ladder_2025-01-01_1d_horizon-4_fast.csv",
        "run_meta.csv", "window_stats.csv"]
    rows = meta(out)
    assert sorted(rows.loc[rows["item"] == "command", "value"]) == [
        "decompose --year 2025 --days 1", "ladder --year 2025 --days 1 --horizon 4 --fast"]


def test_experiments_that_differ_in_an_option_have_names_of_their_own():
    """Regression test: experiments() named a command line by its command and window only, so
    lines that differed in --horizon or --neg-rule became one experiment, the last one."""
    from hybrid import report

    named = report.experiments(("ladder --year 2025 --days 30",
                                "ladder --year 2025 --days 30 --horizon 48",
                                "ladder --year 2025 --days 30 --neg-rule six_hour --fast"))
    assert list(named) == ["ladder_2025-01-01_30d", "ladder_2025-01-01_30d_horizon-48",
                           "ladder_2025-01-01_30d_neg-rule-six_hour_fast"]


def test_one_experiment_given_twice_is_refused():
    """Regression test: written two ways, one experiment was kept once without a word."""
    from hybrid import report

    with pytest.raises(ValueError, match="ladder_2025-01-01_30d"):
        report.experiments(("ladder --year 2025 --days 30 --horizon 48",
                            "ladder --year 2025 --start 01-01 --days 30 --horizon 48"))


def _load_afrr_fixtures():
    """tests/afrr_fixtures.py, loaded by its path: the root pyproject.toml runs the tests in
    importlib mode, which puts no test folder on sys.path."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("afrr_fixtures.py")
    spec = importlib.util.spec_from_file_location("afrr_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_meta_records_the_afrr_source_and_rules(stand_ins, monkeypatch, tmp_path):
    """Feature test: an aFRR experiment rests on a regelleistung.net results file and on rule
    documents checked for its year. run_meta names, for each experiment that read such a file,
    its URL and SHA-256, and the documents of that year: in 2024 the PQ conditions changed
    from v1.04 to v1.05, and BK6-23-333 governs 2025 only, as the procurement with the Czech
    TSO that it approved began on 3 September 2025. Another experiment of the same report
    gains no section."""
    fixtures = _load_afrr_fixtures()
    files = {year: fixtures.capacity_results(year) for year in (2024, 2025)}
    monkeypatch.setattr(cli, "load_capacity_results", lambda year, cache_dir=None: files[year])
    commands = tmp_path / "set.txt"
    commands.write_text("afrr --year 2025 --days 1 --fast\nafrr --year 2024 --days 1 --fast\n"
                        "ladder --year 2025 --days 1 --horizon 4 --fast\n", encoding="utf-8")
    assert cli.main(["report", "--commands", str(commands),
                     "--reports-dir", str(tmp_path)]) == 0

    rows = meta(tmp_path)
    sections = rows.groupby("experiment")["section"].unique().map(set)
    on_data = {"run", "code", "environment", "input_sha256", "source_url", "capacity_mw",
               "market_value_proxy_eur_mwh"}
    assert sections["ladder_2025-01-01_1d_horizon-4_fast"] == on_data
    rules = {}
    for year in (2024, 2025):
        mine = rows[rows["experiment"] == f"afrr_{year}-01-01_1d_fast"]
        assert set(mine["section"]) == on_data | {"afrr_source", "afrr_rules"}
        source = mine[mine["section"] == "afrr_source"].set_index("item")["value"].to_dict()
        assert source == {f"{year} url": files[year].url, f"{year} sha256": files[year].sha256}
        rules[year] = mine[mine["section"] == "afrr_rules"].set_index("item")["value"].to_dict()
    assert rules[2025] == {"PQ conditions for FCR, aFRR and mFRR, v1.05": "5 July 2024",
                           "MfRRA, reading version": "8 December 2022",
                           "BNetzA BK6-15-158": "13 June 2017",
                           "BNetzA BK6-23-333": "5 November 2024"}
    assert rules[2024] == {"PQ conditions for FCR, aFRR and mFRR, v1.04": "3 June 2022",
                           "PQ conditions for FCR, aFRR and mFRR, v1.05": "5 July 2024",
                           "MfRRA, reading version": "8 December 2022",
                           "BNetzA BK6-15-158": "13 June 2017"}


def test_afrr_experiments_are_named_after_their_options():
    """The aFRR experiments the README is to quote, named after window and options: --basis
    and --up-hours appear only where they are off their defaults, "bid" and 4 hours, so a
    line that spells out a default is the same experiment as one that leaves it out."""
    from hybrid import report

    named = report.experiments((
        "afrr --year 2024 --full-year --fast", "afrr --year 2025 --full-year --fast",
        "afrr --year 2024 --full-year --fast --up-hours 1",
        "afrr --year 2025 --full-year --fast --up-hours 1",
        "afrr --year 2025 --full-year --fast --basis average",
        "afrr --year 2025 --full-year --fast --basis marginal",
        "afrr --year 2025 --start 01-01 --days 30", "afrr --year 2025 --start 07-01 --days 30",
        "afrr --year 2024 --start 01-01 --days 30", "afrr --year 2024 --start 07-01 --days 30"))
    assert list(named) == [
        "afrr_2024_fast", "afrr_2025_fast", "afrr_2024_fast_up-hours-1",
        "afrr_2025_fast_up-hours-1", "afrr_2025_fast_basis-average",
        "afrr_2025_fast_basis-marginal", "afrr_2025-01-01_30d", "afrr_2025-07-01_30d",
        "afrr_2024-01-01_30d", "afrr_2024-07-01_30d"]
    with pytest.raises(ValueError, match="one experiment, afrr_2025_fast$"):
        report.experiments(("afrr --year 2025 --full-year --fast",
                            "afrr --year 2025 --full-year --fast --basis bid --up-hours 4.0"))


def test_the_publish_set_is_the_one_the_readme_needs():
    """The pinned experiments, named after command, window and options, and the folder they
    go to."""
    from hybrid import report

    assert list(report.experiments()) == [
        "sizing_2024", "sizing_2025", "negprice_2024", "negprice_2025", "decompose_2024",
        "decompose_2025", "decompose_2024_conn-30", "decompose_2025_conn-30",
        "ladder_2025-01-01_30d", "ladder_2025-07-01_30d", "b3_ablation_synthetic_demo"]
    assert report.REPORTS_DIR.parent.name == "02-utility-hybrid-plant-dispatch"


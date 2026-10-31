"""The CSV each command writes with --out: one row per scenario and rung, with every
settlement term, the window and the settings it ran with.

Offline: load_year is replaced by synthetic calendar years, and B3 by a stand-in or a tiny
window, since each CBC call costs about half a second.
"""
from __future__ import annotations

from dataclasses import fields

import numpy as np
import pandas as pd
import pytest

import hybrid.baselines as baselines
import hybrid.cli as cli
from hybrid.config import MarketConfig, PlantConfig, RunConfig

B0, B1, B2, B3 = ("B0 no battery", "B1 curtailment avoidance", "B2 perfect foresight",
                  "B3 rolling MPC")
B2NB = "B2 no battery"
# Columns every result table carries.
COMMON = ["rung", "spot_revenue_eur", "premium_revenue_eur", "marketing_fee_eur",
          "degradation_cost_eur", "imbalance_cost_eur", "import_cost_eur", "net_revenue_eur",
          "chosen_curtail_mwh", "forced_curtail_mwh", "export_mwh", "violations",
          "final_soc_mwh", "premium_period", "premium_wind_mean_eur_mwh",
          "premium_pv_mean_eur_mwh", "premium_rate_mean_eur_mwh", "inputs", "year",
          "window_start_utc", "window_end_utc", "window_start_berlin", "window_end_berlin",
          "days", "steps", "dt_h", "plant_wind_mw", "plant_pv_mw", "plant_bess_mw",
          "plant_bess_mwh", "plant_conn_mw", "plant_c_deg_eur_mwh",
          "market_anzulegender_wert_wind", "market_anzulegender_wert_pv",
          "market_direct_marketing_fee", "market_negative_price_rule"]


def calendar_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """A synthetic Berlin calendar year, cut to its first `days` days as load_year cuts it.

    Wind and PV vary within the day and the price swings between about 20 and 100 EUR/MWh,
    except at 13:00, where it dips below zero for a quarter-hour. So the battery and
    curtailment at negative prices are each worth something. December's prices run 40 EUR/MWh
    higher, so that its premium, which B3 plans January on, differs from the annual one.
    """
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    idx = pd.date_range(start, end, freq=f"{int(dt * 60)}min", inclusive="left")
    n = len(idx)
    h = np.arange(n) * dt % 24
    pv = 0.7 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    december = idx.tz_convert("Europe/Berlin").month == 12
    df = pd.DataFrame({
        "wind_cf": np.clip(0.5 + 0.4 * np.sin(np.arange(n) / 30), 0, 1),
        "pv_cf": pv,
        "pv_feed_in_cf": 0.8 * pv,
        "price_eur_mwh": (60 + 40 * np.sin(2 * np.pi * (h - 18) / 24) - 120 * (h == 13)
                          + 40 * december),
    }, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


def flat_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """`calendar_year` at a flat 100 EUR/MWh, above every applicable value: no premium."""
    df = calendar_year(year, days, dt)
    df["price_eur_mwh"] = 100.0
    return df


def result_table(monkeypatch, tmp_path, argv: list[str], loader=calendar_year) -> pd.DataFrame:
    """Run a command on synthetic data with --out and read back the CSV it wrote."""
    monkeypatch.setattr(cli, "load_year", loader)
    out = tmp_path / "result.csv"
    assert cli.main([*argv, "--out", str(out)]) == 0
    return pd.read_csv(out)


def assert_terms_add_up(df: pd.DataFrame) -> None:
    """Net revenue is the sum of its settlement terms on every row."""
    total = (df["spot_revenue_eur"] + df["premium_revenue_eur"] - df["marketing_fee_eur"]
             - df["degradation_cost_eur"] - df["imbalance_cost_eur"] - df["import_cost_eur"])
    assert df["net_revenue_eur"].to_numpy() == pytest.approx(total.to_numpy())


def test_the_ladder_table_records_b3s_solve_times_and_settings(monkeypatch, tmp_path):
    """Feature test: the ladder printed B3's solve times and settings, but wrote nothing.

    B3 is replaced by B2's dispatch with two known solve times, so the test needs no B3 solve.
    """
    def timed_b2(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, export_cap=None,
                 progress=False, premium=None):
        res = baselines.b2_perfect(wind, pv, eff_price, run, export_cap)
        return {**res, "solve_time_s": np.array([0.25, 0.75])}

    monkeypatch.setattr(baselines, "b3_rolling", timed_b2)
    df = result_table(monkeypatch, tmp_path, ["ladder", "--year", "2025", "--days", "1",
                                              "--horizon", "8"])

    assert set(COMMON) <= set(df.columns)
    assert_terms_add_up(df)
    rows = df.set_index("rung")
    assert list(rows.index) == [B0, B1, B2, B3]
    b3 = rows.loc[B3]
    assert (b3["b3_solve_time_mean_s"], b3["b3_solve_time_max_s"]) == (0.5, 0.75)
    assert rows.loc[[B0, B1, B2], "b3_solve_time_mean_s"].isna().all()
    for column, value in (("horizon_steps", 8), ("forecast_sigma", 0.15), ("forecast_rho", 0.8),
                          ("seed", 0), ("terminal_value", False)):
        assert (rows[column] == value).all(), column
    assert b3["forecast_price_scale"] == pytest.approx(np.std(calendar_year(2024)["price_eur_mwh"]))
    assert b3["b3_premium_rate_mean_eur_mwh"] < b3["premium_rate_mean_eur_mwh"] - 1.0, \
        "B3 plans on December's lower rate and is settled on the annual one"
    assert (b3["window_start_berlin"], b3["window_end_berlin"]) == (
        "2025-01-01T00:00:00+01:00", "2025-01-02T00:00:00+01:00")
    assert (b3["window_start_utc"], b3["year"], b3["days"], b3["steps"]) == (
        "2024-12-31T23:00:00+00:00", 2025, 1.0, 96)


def test_sizing_splits_the_battery_from_curtailing_at_low_prices(monkeypatch, tmp_path):
    """Feature test: the battery's value was B2 minus B0, which also counts what curtailing at
    low prices earns, and that needs no battery. B2 with the battery taken out splits the two.

    The synthetic price dips below zero once a day, so both parts are worth something here.
    """
    df = result_table(monkeypatch, tmp_path, ["sizing", "--year", "2025", "--days", "1"])

    assert set(COMMON + ["conn_mw", "overbuild", "battery_value_eur",
                         "chosen_curtailment_value_eur"]) <= set(df.columns)
    assert_terms_add_up(df)
    assert len(df) == 5 * 4, "five connections, four rungs"
    assert sorted(df["conn_mw"].unique()) == [20.0, 25.0, 30.0, 40.0, 60.0]
    assert (df["violations"] == 0).all()
    for conn, g in df.groupby("conn_mw"):
        net = g.set_index("rung")["net_revenue_eur"]
        assert set(net.index) == {B0, B1, B2, B2NB}
        tol = 1e-6 * abs(net[B2])
        assert net[B2] >= net[B2NB] - tol, f"{conn} MW: the battery cannot cost B2 money"
        assert net[B2NB] >= net[B0] - tol, f"{conn} MW: B0's dispatch is open to B2 no battery"
        assert g["battery_value_eur"].to_numpy() == pytest.approx(np.full(4, net[B2] - net[B2NB]))
        assert g["chosen_curtailment_value_eur"].to_numpy() == pytest.approx(
            np.full(4, net[B2NB] - net[B0]))
        assert g["overbuild"].to_numpy() == pytest.approx(np.full(4, 80.0 / conn))
        assert (g["plant_conn_mw"] == conn).all()
    b2 = df[df["rung"] == B2].set_index("conn_mw")
    assert (b2["chosen_curtailment_value_eur"] > 0).all()
    assert b2.loc[60.0, "battery_value_eur"] > 0, "the default connection leaves headroom"
    assert (df.loc[df["rung"] == B2NB, "final_soc_mwh"] < 1e-6).all(), "it has no battery"


def test_the_negprice_table_names_the_year_its_note_is_about(monkeypatch, tmp_path):
    """The note on which vintage gets no premium belongs in the table, named by the evaluated
    year's own periods. (An old version of the printed note said "In 2024" whatever year ran.)

    At a flat 100 EUR/MWh every market value is 100, above both vintages' applicable values.
    """
    df = result_table(monkeypatch, tmp_path, ["negprice", "--year", "2025", "--days", "1"],
                      flat_year)

    assert set(COMMON + ["vintage", "rule", "no_premium"]) <= set(df.columns)
    assert len(df) == 2 * 5 * 3, "two vintages, five rules in 2025, three rungs"
    assert (df["year"] == 2025).all()
    notes = df.groupby("vintage")["no_premium"].unique()
    assert list(notes["AW 73.5 (recent onshore)"]) == ["wind in 2025, PV in 2025"]
    assert list(notes["AW 95.0 (older vintage)"]) == ["wind in 2025-01, PV in 2025-01"]
    by_vintage = df.groupby("vintage")
    assert by_vintage["premium_period"].unique().to_dict() == {
        "AW 73.5 (recent onshore)": ["year"], "AW 95.0 (older vintage)": ["month"]}
    assert by_vintage["market_anzulegender_wert_wind"].unique().to_dict() == {
        "AW 73.5 (recent onshore)": [73.5], "AW 95.0 (older vintage)": [95.0]}
    assert (df["premium_rate_mean_eur_mwh"] == 0.0).all()


def test_the_decompose_table_holds_both_connections_and_the_split(monkeypatch, tmp_path):
    df = result_table(monkeypatch, tmp_path, ["decompose", "--year", "2025", "--days", "1"])

    assert set(COMMON) <= set(df.columns)
    assert_terms_add_up(df)
    net = df.set_index(["scenario", "rung"])["net_revenue_eur"]
    as_built = net["as built", B2] - net["as built", B0]
    unconstrained = net["unconstrained connection", B2] - net["unconstrained connection", B0]
    assert df.groupby("scenario")["plant_conn_mw"].first().to_dict() == {
        "as built": 60.0, "unconstrained connection": 200.0}
    assert df["value_as_built_eur"].to_numpy() == pytest.approx(np.full(6, as_built))
    assert df["value_unconstrained_eur"].to_numpy() == pytest.approx(np.full(6, unconstrained))
    assert df["attributable_to_curtailment_eur"].to_numpy() == pytest.approx(
        np.full(6, as_built - unconstrained))


def tiny_demo():
    """Inputs short enough for B3 in a test, in the shape of `synthetic.demo_inputs`: two hours
    of spill at 20 EUR/MWh, then two hours of headroom at 90, behind a 20 MW connection, on a
    30-minute horizon."""
    from hybrid import synthetic

    run = RunConfig(dt=0.25, horizon_steps=2, plant=PlantConfig(conn_mw=20.0))
    wind = np.r_[np.full(8, 30.0), np.full(8, 10.0)]
    price = np.r_[np.full(8, 20.0), np.full(8, 90.0)]
    return synthetic.DemoInputs(wind, np.zeros(16), price, run, 8.0)


def test_the_b3_ablation_reports_recovery_and_b3s_end_state(monkeypatch, tmp_path):
    """Feature test: the README's B3 figures (100 %, 88 %, -179 %, 34 to 38 MWh) came from a
    scratch run that no command could repeat. b3-ablation crosses forecast error with the
    terminal value and writes each case's ladder, B3's recovery and its end state of charge."""
    from hybrid import synthetic

    ends = {}

    def b3_end_recorded(wind, pv, price, run, *args, **kwargs):
        res = baselines.run_ladder(wind, pv, price, run, *args, **kwargs)
        ends[(run.forecast_sigma, run.terminal_value)] = float(res[B3]["soc"][-1])
        return res

    monkeypatch.setattr(synthetic, "demo_inputs", tiny_demo)
    monkeypatch.setattr(cli, "run_ladder", b3_end_recorded)
    df = result_table(monkeypatch, tmp_path, ["b3-ablation", "--synthetic-demo"])

    assert set(COMMON + ["forecast_sigma", "terminal_value", "recovery",
                         "b3_solve_time_mean_s"]) <= set(df.columns)
    assert_terms_add_up(df)
    assert len(df) == 4 * 4, "four cases, four rungs"
    cases = df.groupby(["forecast_sigma", "terminal_value"])
    assert sorted(cases.groups) == [(0.0, False), (0.0, True), (0.15, False), (0.15, True)]
    for case, g in cases:
        net = g.set_index("rung")["net_revenue_eur"]
        assert net[B2] - net[B1] > 1.0, f"{case}: B2 leaves B1 headroom"
        expected = (net[B3] - net[B1]) / (net[B2] - net[B1])
        assert g["recovery"].to_numpy() == pytest.approx(np.full(4, expected)), case
        end = g.set_index("rung").loc[B3, "final_soc_mwh"]
        assert end == pytest.approx(ends[case]), f"{case}: B3's stored energy after its last step"
    for sigma in (0.0, 0.15):
        # the linear terminal value makes B3 hoard (`default_terminal_price`), so a column that
        # held the start, 2 MWh in every case, could not pass the check above
        assert ends[(sigma, True)] > ends[(sigma, False)] + 1.0, sigma
    assert (df["inputs"] == "synthetic (showcase demo_05)").all()
    assert df["window_start_utc"].isna().all(), "the demo has no dates"
    assert (df["premium_rate_mean_eur_mwh"] == 8.0).all()


def test_the_b3_ablation_runs_on_a_window_of_data_as_the_ladder_does(monkeypatch, tmp_path):
    """On --year/--days the ablation settles every rung as ladder does and plans B3 on the
    month before's rate; B3 is replaced by B2's dispatch so that nothing but B2 is solved."""
    def b2_instead(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, export_cap=None,
                   progress=False, premium=None):
        return {**baselines.b2_perfect(wind, pv, eff_price, run, export_cap),
                "solve_time_s": np.array([0.5])}

    monkeypatch.setattr(baselines, "b3_rolling", b2_instead)
    df = result_table(monkeypatch, tmp_path, ["b3-ablation", "--year", "2025", "--days", "1"])

    assert set(COMMON + ["recovery", "b3_premium_rate_mean_eur_mwh"]) <= set(df.columns)
    assert len(df) == 4 * 4
    assert (df["window_start_berlin"] == "2025-01-01T00:00:00+01:00").all()
    assert (df["premium_period"] == "year").all()
    assert df["forecast_price_scale"].to_numpy() == pytest.approx(
        np.full(16, np.std(calendar_year(2024)["price_eur_mwh"])))


def test_the_demo_inputs_are_those_of_showcase_demo_05(monkeypatch):
    """The ablation's synthetic inputs must be the showcase's, value for value, so that the two
    report one result. showcase.py builds them inside demo_05; a stand-in for run_ladder
    records what demo_05 hands it, and nothing is solved."""
    import importlib.util
    import io
    import sys
    from pathlib import Path

    from hybrid import synthetic

    calls = []

    def recording_ladder(wind, pv, price, run, premium_rate=None, **kwargs):
        calls.append((wind, pv, price, run, premium_rate, kwargs))
        cost = {"net_revenue": 1.0, "forced_curtail_mwh": 0.0}
        return {name: {"cost": cost} for name in (B0, B3)}

    monkeypatch.setattr(baselines, "run_ladder", recording_ladder)
    monkeypatch.setattr(sys, "path", list(sys.path))      # showcase.py adds project folders
    monkeypatch.setattr(sys, "stdout", io.StringIO())     # and sets the console's encoding
    path = Path(__file__).resolve().parents[3] / "showcase.py"
    spec = importlib.util.spec_from_file_location("showcase_under_test", path)
    showcase = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(showcase)
    showcase.demo_05(False)

    (wind, pv, price, run, premium_rate, kwargs), = calls
    demo = synthetic.demo_inputs()
    for name, got, want in (("wind", wind, demo.wind), ("PV", pv, demo.pv),
                            ("price", price, demo.price)):
        assert np.array_equal(got, want), name
    assert run == demo.run
    assert (premium_rate, kwargs) == (demo.premium_rate, {})


def test_window_stats_count_what_the_readme_cites():
    """The caveats name negative and below-fee quarter-hours, the peak, the largest combined
    capacity factor and whether the connection binds; each must come from a table."""
    from hybrid import tables

    run = RunConfig()          # 50 MW wind + 30 MWp PV behind 60 MW, a 3 EUR/MWh fee
    row = tables.window_stats("w", np.array([10.0, 40.0, 45.0, 20.0]),
                              np.array([0.0, 25.0, 20.0, 0.0]),
                              np.array([-5.0, 2.0, 50.0, 3.0]), run, {"days": 1 / 24})
    assert (row["window"], row["days"], row["mean_price_eur_mwh"]) == ("w", 1 / 24, 12.5)
    assert (row["negative_price_quarter_hours"], row["below_fee_quarter_hours"]) == (1, 2)
    assert (row["peak_generation_mw"], row["max_combined_cf"]) == (65.0, 65.0 / 80.0)
    assert (row["connection_mw"], row["steps_over_connection"], row["connection_binds"]) == (
        60.0, 2, True)


def test_merged_rows_replace_their_own_and_keep_the_others(tmp_path):
    """A shared table must gather the rows of parts run apart, replace a part's rows when it
    reruns, keep the other parts' rows as written, and sort by the key whatever came first."""
    from hybrid import tables

    path = tmp_path / "meta" / "run_meta.csv"
    tables.merge_csv(path, [{"experiment": "b", "item": "x", "value": 0.1}], "experiment")
    tables.merge_csv(path, [{"experiment": "a", "item": "x", "value": 1.0},
                            {"experiment": "a", "item": "y", "value": 2.0}], "experiment")
    tables.merge_csv(path, [{"experiment": "a", "item": "x", "value": 3.0}], "experiment")

    assert path.read_text() == "experiment,item,value\na,x,3.0\nb,x,0.1\n"


def test_processes_side_by_side_lose_no_rows(tmp_path):
    """Two processes merge into one table at the same time; the lock keeps every row."""
    import os
    import subprocess
    import sys

    path = tmp_path / "run_meta.csv"
    script = ("import sys; from pathlib import Path; from hybrid import tables\n"
              "for i in range(20):\n"
              "    tables.merge_csv(Path(sys.argv[1]), [{'part': sys.argv[2] + str(i)}], 'part')\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    procs = [subprocess.Popen([sys.executable, "-c", script, str(path), part], env=env)
             for part in ("a", "b")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    assert len(pd.read_csv(path)) == 40


def test_every_setting_is_recorded_or_named_inert():
    """A setting a table leaves out must be one no command reads, or a row could not be
    re-run from what it records; a new setting has to be put on one of the lists."""
    from hybrid import tables

    recorded = set(tables.PLANT_SETTINGS) | set(tables.MARKET_SETTINGS) | set(tables.B3_SETTINGS)
    declared = {f.name for cls in (PlantConfig, MarketConfig, RunConfig) for f in fields(cls)}
    # the step length is a window column, and plant and market are recorded field by field
    assert recorded | set(tables.INERT_SETTINGS) | {"dt", "plant", "market"} == declared
    assert not recorded & set(tables.INERT_SETTINGS)

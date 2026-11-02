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
from hybrid.plant import simulate

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
    # B3's settings carry its name, as the other rungs' rows of the run hold them too
    for column, value in (("b3_horizon_steps", 8), ("b3_forecast_sigma", 0.15),
                          ("b3_forecast_rho", 0.8), ("b3_seed", 0), ("b3_terminal_value", False)):
        assert (rows[column] == value).all(), column
    assert not {"horizon_steps", "forecast_sigma", "forecast_rho", "forecast_price_scale", "seed",
                "terminal_value", "terminal_price"} & set(df.columns)
    assert b3["b3_forecast_price_scale"] == pytest.approx(
        np.std(calendar_year(2024)["price_eur_mwh"]))
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


def test_a_row_without_a_battery_records_the_plant_it_ran_on(monkeypatch, tmp_path):
    """Regression test: the B0 and B2 no battery rows read plant_bess_mw 20 and plant_bess_mwh
    40, a battery they ran without; they ran on a 0 MW, 1e-6 MWh stand-in."""
    df = result_table(monkeypatch, tmp_path, ["sizing", "--year", "2025", "--days", "1"])

    none = df[df["rung"].isin([B0, B2NB])]
    assert (none["plant_bess_mw"] == 0.0).all()
    assert none["plant_bess_mwh"].to_numpy() == pytest.approx(np.full(len(none), 1e-6))
    built = df[df["rung"].isin([B1, B2])]
    assert ((built["plant_bess_mw"] == 20.0) & (built["plant_bess_mwh"] == 40.0)).all()


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


def test_negprice_writes_each_vintages_premium_by_period(monkeypatch, tmp_path):
    """Regression test: the older vintage is paid against each month's market value, but its
    rows kept only the window's mean rate, so its monthly premiums, which the README quotes,
    traced to no file. negprice now writes its premium tables beside its result, one row per
    vintage and period. The window, 30 January to 1 February, spans two months."""
    from hybrid import data
    from hybrid.market import market_values

    df = result_table(monkeypatch, tmp_path,
                      ["negprice", "--year", "2025", "--start", "01-30", "--days", "3"])
    premium = pd.read_csv(tmp_path / "result_premium.csv")

    older, recent = "AW 95.0 (older vintage)", "AW 73.5 (recent onshore)"
    assert premium.groupby("vintage")["period"].apply(list).to_dict() == {
        older: ["2025-01", "2025-02"], recent: ["2025"]}
    assert (premium["window_start_berlin"] == "2025-01-30T00:00:00+01:00").all()
    for row in premium.itertuples():
        assert row.premium_wind_eur_mwh == pytest.approx(
            max(0.0, row.applicable_wind_eur_mwh - row.market_value_wind_eur_mwh))
        assert row.premium_pv_eur_mwh == pytest.approx(
            max(0.0, row.applicable_pv_eur_mwh - row.market_value_pv_eur_mwh))
    year = calendar_year(2025)
    monthly = market_values(year["price_eur_mwh"], data.market_value_weights(year), "month")
    months = premium[premium["vintage"] == older].set_index("period")
    assert months["market_value_wind_eur_mwh"].to_numpy() == pytest.approx(
        monthly.loc[["2025-01", "2025-02"], "wind"].to_numpy())
    assert months["premium_wind_eur_mwh"].min() > 0, "a premium the table would average away"
    # the rows' mean rate weights each month's by the window's days in it: two and one
    rate = months["plant_rate_eur_mwh"]
    assert df.loc[df["vintage"] == older, "premium_rate_mean_eur_mwh"].to_numpy() == (
        pytest.approx((2 * rate["2025-01"] + rate["2025-02"]) / 3))


def test_negprice_writes_the_premium_paid_per_technology(monkeypatch, tmp_path):
    """Feature test: negprice settles every rung on one blended premium rate per period,
    which pays each step the period's wind/PV mix. A rule suspends the premium in steps of
    their own mix, so the blend misstates what the rule removes. negprice now also writes the
    premium a plant that exports all of its generation is paid per technology, as the law pays.

    At half the wind of `calendar_year` the plant never reaches 60 MW, so B0 exports all of
    its generation. The price dips below zero at 13:00 for one quarter-hour: new_2025 suspends
    that step, the hourly rules do not. PV makes more of the output then than over the day,
    and its premium is the higher one, so per technology the rule removes more.
    """
    def half_wind(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
        df = calendar_year(year, days, dt)
        df["wind_cf"] *= 0.5
        return df

    def idle_b2(wind, pv, eff_price, run, export_cap=None):
        # no CBC solve: the battery stays empty and nothing is curtailed, as B1 does here
        n = len(wind)
        return simulate(wind, pv, np.zeros(n), np.zeros(n), run.dt, run.plant,
                        export_cap=export_cap)

    monkeypatch.setattr(baselines, "b2_perfect", idle_b2)
    df = result_table(monkeypatch, tmp_path, ["negprice", "--year", "2025", "--days", "1"],
                      half_wind)
    premium = pd.read_csv(tmp_path / "result_premium.csv").set_index("vintage")

    day = half_wind(2025, 1)
    wind, pv = 50.0 * day["wind_cf"].to_numpy(), 30.0 * day["pv_cf"].to_numpy()
    dip = day["price_eur_mwh"].to_numpy() < 0
    assert dip.sum() == 1
    b0 = df[df["rung"] == B0].set_index(["vintage", "rule"])
    assert (b0["forced_curtail_mwh"] == 0).all(), "B0 exports all of its generation"
    for vintage, p in premium.iterrows():           # one period each: a day of January
        def paid(mask, p=p):
            return (np.sum(wind[mask]) * p["premium_wind_eur_mwh"]
                    + np.sum(pv[mask]) * p["premium_pv_eur_mwh"]) * 0.25
        exact = b0.loc[vintage, "full_export_premium_by_technology_eur"]
        whole = paid(np.ones(len(dip), dtype=bool))
        assert exact["none"] == pytest.approx(whole)
        for rule in ("six_hour", "four_hour", "three_hour"):
            assert exact[rule] == pytest.approx(whole), f"{vintage}, {rule}: one hour is short"
        assert exact["new_2025"] == pytest.approx(whole - paid(dip))
        # where nothing is suspended the blend is exact, and elsewhere it removes less
        blended = b0.loc[vintage, "premium_revenue_eur"]
        assert blended["none"] == pytest.approx(whole)
        assert paid(dip) > blended["none"] - blended["new_2025"] > 0, vintage


def test_decompose_splits_the_battery_from_curtailing_at_low_prices(monkeypatch, tmp_path,
                                                                   capsys):
    """Regression test: decompose called B2 minus B0 the battery's value and, at a connection
    that never binds, "pure arbitrage". Both held what curtailing at low prices earns, which
    needs no battery and which sizing already splits off with "B2 no battery".

    The synthetic price dips below zero once a day, so that part is worth something here in
    both scenarios, and the 60 MW connection binds, so the connection limit is too.
    """
    df = result_table(monkeypatch, tmp_path, ["decompose", "--year", "2025", "--days", "1"])

    assert "pure arbitrage" not in capsys.readouterr().out
    split = ["battery_value_eur", "chosen_curtailment_value_eur", "connection_limit_effect_eur",
             "connection_limit_share"]
    assert set(COMMON + ["scenario", *split]) <= set(df.columns)
    assert not {"value_as_built_eur", "value_unconstrained_eur",
                "attributable_to_curtailment_eur", "curtailment_share"} & set(df.columns)
    assert_terms_add_up(df)
    assert df.groupby("scenario")["plant_conn_mw"].first().to_dict() == {
        "as built": 60.0, "unconstrained connection": 200.0}
    battery = {}
    for scenario, g in df.groupby("scenario"):
        net = g.set_index("rung")["net_revenue_eur"]
        assert set(net.index) == {B0, B1, B2, B2NB}, scenario
        battery[scenario] = net[B2] - net[B2NB]
        chosen = net[B2NB] - net[B0]
        assert chosen > 0, f"{scenario}: curtailing below zero pays, battery or not"
        assert g["battery_value_eur"].to_numpy() == pytest.approx(np.full(4, battery[scenario]))
        assert g["chosen_curtailment_value_eur"].to_numpy() == pytest.approx(np.full(4, chosen))
        assert net[B2] - net[B0] == pytest.approx(battery[scenario] + chosen), scenario
    effect = battery["as built"] - battery["unconstrained connection"]
    assert abs(effect) > 1.0, "the 60 MW connection binds on this synthetic day"
    assert df["connection_limit_effect_eur"].to_numpy() == pytest.approx(np.full(8, effect))
    assert df["connection_limit_share"].to_numpy() == pytest.approx(
        np.full(8, effect / battery["as built"]))


def test_decompose_splits_at_the_connection_it_is_given(monkeypatch, tmp_path):
    """Feature test: decompose split the battery's value at the default 60 MW only, which the
    national data's generation never reaches, so the connection limit's effect was always 0
    there. --conn sets the connection as built; here 30 MW, where B0 spills."""
    df = result_table(monkeypatch, tmp_path,
                      ["decompose", "--year", "2025", "--days", "1", "--conn", "30"])

    assert df.groupby("scenario")["plant_conn_mw"].first().to_dict() == {
        "as built": 30.0, "unconstrained connection": 200.0}
    built = df[df["scenario"] == "as built"].set_index("rung")
    assert built.loc[B0, "forced_curtail_mwh"] > 1.0, "the connection binds"
    assert abs(built.loc[B0, "connection_limit_effect_eur"]) > 1.0
    with pytest.raises(SystemExit):
        cli.main(["decompose", "--conn", "0"])


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

    assert set(COMMON + ["b3_forecast_sigma", "b3_terminal_value", "recovery",
                         "b3_solve_time_mean_s"]) <= set(df.columns)
    assert_terms_add_up(df)
    assert len(df) == 4 * 4, "four cases, four rungs"
    cases = df.groupby(["b3_forecast_sigma", "b3_terminal_value"])
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


def test_a_row_records_the_values_its_settings_leave_to_the_run():
    """Feature test: where a setting is None, the rows recorded only the switch. B3 sized its
    price error on the first horizon's effective price, valued stored energy at each plan's
    upper quartile times eta_d, and None in soc_init_frac starts the battery at its minimum.
    The rows now hold each of these values, and the ladder's tolerance, within which B3 may
    sit above B2 by the solver's rounding."""
    from hybrid import tables
    from hybrid.market import effective_price

    demo = tiny_demo()
    price = demo.price + np.tile([0.0, 4.0], 8)          # a spread within the first horizon
    window = tables.window_columns("test", None, len(price), demo.run.dt)
    for terminal in (False, True):
        run = demo.run.with_(forecast_sigma=0.0, terminal_value=terminal)
        res = baselines.run_ladder(demo.wind, demo.pv, price, run, demo.premium_rate)
        rows = {row["rung"]: row for row in tables.rung_rows(res, run, window, {})}

        eff = effective_price(price, run.market, demo.premium_rate, run.dt)
        assert rows[B3]["b3_price_error_scale_eur_mwh"] == pytest.approx(np.std(eff[:2]))
        # without forecast error each plan prices the true effective prices of its horizon
        plans = [eff[t:t + run.horizon_steps] for t in range(len(price))]
        upper = np.mean([np.percentile(plan, 75) * run.plant.eta_d for plan in plans])
        assert rows[B3]["b3_terminal_price_mean_eur_mwh"] == pytest.approx(
            upper if terminal else 0.0)
        assert rows[B1]["b3_terminal_price_mean_eur_mwh"] is None, "B3's alone"
        assert rows[B2]["plant_soc_init_mwh"] == 2.0, "5 % of 40 MWh"
        assert rows[B0]["plant_soc_init_mwh"] == pytest.approx(5e-8), "the stand-in's minimum"
        tolerance = max(1e-3, 1e-6 * abs(res[B2]["cost"]["net_revenue"]))
        assert {row["ladder_tolerance_eur"] for row in rows.values()} == {tolerance}


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
    assert df["b3_forecast_price_scale"].to_numpy() == pytest.approx(
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


def test_a_lock_or_table_held_for_a_moment_loses_no_rows(monkeypatch, tmp_path):
    """Regression test: on Windows, os.open refuses a lock file that another process is
    deleting with PermissionError, not FileExistsError, and os.replace refuses a table that
    another program holds open for a moment, such as a virus scanner. Either crashed the merge
    and lost its rows: four processes side by side kept 325 and 300 of 400 rows in two runs.
    Here each is refused once, as Windows refuses it."""
    import os

    from hybrid import tables

    real_open, real_replace = os.open, os.replace
    refusals = {"open": 1, "replace": 1}

    def busy(name, real):
        def call(path, *args):
            if refusals[name] and (name == "replace" or str(path).endswith(".lock")):
                refusals[name] -= 1
                raise PermissionError(13, "Permission denied", str(path))
            return real(path, *args)
        return call

    monkeypatch.setattr(os, "open", busy("open", real_open))
    monkeypatch.setattr(os, "replace", busy("replace", real_replace))
    path = tmp_path / "run_meta.csv"
    tables.merge_csv(path, [{"experiment": "a", "value": 1.0}], "experiment")

    assert refusals == {"open": 0, "replace": 0}, "each was refused once"
    assert path.read_text() == "experiment,value\na,1.0\n"


def test_every_setting_is_recorded_or_named_inert():
    """A setting a table leaves out must be one no command reads, or a row could not be
    re-run from what it records; a new setting has to be put on one of the lists."""
    from hybrid import tables

    recorded = (set(tables.PLANT_SETTINGS) | set(tables.MARKET_SETTINGS)
                | set(tables.AFRR_SETTINGS) | set(tables.B3_SETTINGS)
                | set(tables.B3_AFRR_SETTINGS))
    declared = {f.name for cls in (PlantConfig, MarketConfig, RunConfig) for f in fields(cls)}
    # the step length is a window column, and plant and market are recorded field by field
    assert recorded | set(tables.INERT_SETTINGS) | {"dt", "plant", "market"} == declared
    assert not recorded & set(tables.INERT_SETTINGS)


def test_the_afrr_settings_are_recorded_only_where_afrr_is_enabled():
    """A row records the aFRR settings as market_* columns where aFRR is enabled, and only
    there, so that every table of a run without aFRR, the committed ones included, keeps the
    columns it had. On 87ce39a MarketConfig has neither afrr_price_basis nor the energy
    bands, so the settings cannot be recorded."""
    from dataclasses import replace

    from hybrid import tables

    run = RunConfig()
    off = tables.settings(run, with_b3=False)
    on = tables.settings(run.with_(market=replace(run.market, afrr_enabled=True,
                                                  afrr_price_basis="marginal")), with_b3=True)

    assert not [col for col in off if col.startswith("market_afrr_")]
    assert {col: value for col, value in on.items() if col.startswith("market_afrr_")} == {
        "market_afrr_enabled": True, "market_afrr_price_basis": "marginal",
        "market_afrr_energy_hours_up": 4.0, "market_afrr_energy_hours_down": 1.0,
        "market_afrr_floor_hours": 0.25, "market_afrr_shortfall_penalty_eur_mwh": 10_000.0}
    # beside the other market settings, before B3's, and B3's aFRR one after them
    assert list(on) == (list(off) + [f"market_{name}" for name in tables.AFRR_SETTINGS]
                        + [f"b3_{name}" for name in tables.B3_SETTINGS]
                        + [f"b3_{name}" for name in tables.B3_AFRR_SETTINGS])


def test_b3s_afrr_setting_is_recorded_only_where_b3_ran_with_afrr():
    """B3's derating is recorded as b3_afrr_forecast_margin_sigmas where B3 ran with aFRR
    enabled, and nowhere else: not where aFRR is off, as in every committed table, and not
    on a run without B3. On 4eb0fee RunConfig has no such setting."""
    from dataclasses import replace

    from hybrid import tables

    off = RunConfig(afrr_forecast_margin_sigmas=1.5)
    on = off.with_(market=replace(off.market, afrr_enabled=True))
    column = "b3_afrr_forecast_margin_sigmas"

    assert tables.settings(on, with_b3=True)[column] == 1.5
    assert column not in tables.settings(on, with_b3=False)
    assert column not in tables.settings(off, with_b3=True)

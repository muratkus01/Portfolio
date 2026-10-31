"""EEG rules the settlement must follow: the negative-price rule of § 51 by vintage, and the
market premium against the technology's market value (Anlage 1).

Every case is checked against the statute's own wording; `hybrid.market` cites the paragraphs.
"""
from __future__ import annotations

import functools

import numpy as np
import pandas as pd
import pytest

from hybrid import cli, config, market
from hybrid.baselines import run_ladder
from hybrid.config import MarketConfig, RunConfig
from hybrid.market import premium_eligible, settle

HOUR_RULES = [("six_hour", 6), ("four_hour", 4), ("three_hour", 3), ("two_hour", 2),
              ("one_hour", 1)]


def hourly_on_quarter_hours(*hourly: float) -> np.ndarray:
    """Hourly day-ahead prices held across their four quarter-hours, as the data layer does."""
    return np.repeat(np.array(hourly, dtype=float), 4)


# ------------------------------------------------------------------ § 51 negative-price rule
@pytest.mark.parametrize("rule, hours", HOUR_RULES)
def test_a_run_that_reaches_the_threshold_loses_the_premium_throughout(rule, hours):
    """The whole run loses the premium, from its first quarter-hour, once it is long enough."""
    price = hourly_on_quarter_hours(5.0, *[-1.0] * hours, 5.0)
    e = premium_eligible(price, MarketConfig(negative_price_rule=rule))
    assert e[:4].all() and e[-4:].all(), "the positive hours around the run keep it"
    assert not e[4:-4].any(), f"every step of a {hours} h run loses it under {rule}"


@pytest.mark.parametrize("rule, hours", HOUR_RULES[:-1])
def test_a_run_shorter_than_the_threshold_keeps_the_premium(rule, hours):
    price = hourly_on_quarter_hours(5.0, *[-1.0] * (hours - 1), 5.0)
    assert premium_eligible(price, MarketConfig(negative_price_rule=rule)).all()


def test_quarter_hour_prices_count_an_hour_by_its_mean():
    """On 15-minute prices an hour counts as negative when its quarter-hour MEAN is negative.

    § 100 (45) sentence 2 EEG 2023, for the 15-minute day-ahead products the DE-LU market
    uses from 1 Oct 2025. A counted hour then loses the premium as a whole, its one positive
    quarter-hour included; an hour whose mean is positive interrupts the run.
    """
    neg = [-10.0] * 4
    six_hour = MarketConfig(negative_price_rule="six_hour")
    counted = np.array(neg * 2 + [-10.0, -10.0, -10.0, 20.0] + neg * 3)    # mean -2.5
    assert not premium_eligible(counted, six_hour).any()
    split = np.array(neg * 2 + [-10.0, -10.0, -10.0, 40.0] + neg * 3)      # mean +2.5
    assert premium_eligible(split, six_hour).all()


def test_the_rule_counts_hours_whatever_the_step_length():
    """Six negative hourly steps are a six-hour run; eight quarter-hours are only two hours.

    The eight steps are all negative, so as quarter-hours they make two negative hours, a run
    that keeps the premium, and as hourly steps an eight-hour run that loses it.
    """
    six_hour = MarketConfig(negative_price_rule="six_hour")
    hourly = np.array([5.0] + [-1.0] * 6 + [5.0])
    assert list(premium_eligible(hourly, six_hour, dt=1.0)) == [True] + [False] * 6 + [True]
    eight_steps = np.full(8, -1.0)
    assert premium_eligible(eight_steps, six_hour, dt=0.25).all(), "two hours keep it"
    assert not premium_eligible(eight_steps, six_hour, dt=1.0).any(), "eight hours lose it"


def test_a_step_length_that_does_not_divide_an_hour_is_refused():
    with pytest.raises(ValueError, match="step length"):
        premium_eligible(np.zeros(4), MarketConfig(negative_price_rule="six_hour"), dt=0.4)


def test_vintages_carry_the_rule_of_their_commissioning_date():
    """§ 100 EEG 2023 keeps each plant on the § 51 version of its commissioning date."""
    assert market.vintage_rules(2024) == {
        "before 2016": "none",
        "2016-2020": "six_hour",
        "2021-2022": "four_hour",
        "2023 to 24 Feb 2025": "three_hour",
        "from 25 Feb 2025": "new_2025",
    }


@pytest.mark.parametrize("year, rule", [(2023, "four_hour"), (2024, "three_hour"),
                                        (2025, "three_hour"), (2026, "two_hour"),
                                        (2027, "one_hour"), (2030, "one_hour")])
def test_the_2023_vintage_threshold_falls_by_calendar_year(year, rule):
    """§ 51 (1) as in force on 24 Feb 2025: 4 h in 2023, 3 h in 2024 and 2025, 2 h in 2026,
    then 1 h."""
    assert market.vintage_rules(year)["2023 to 24 Feb 2025"] == rule


def test_every_configured_rule_is_implemented():
    """A rule the config accepts must not fail inside the settlement."""
    price = hourly_on_quarter_hours(5.0, -1.0, 5.0)
    for rule in config.NEGATIVE_PRICE_RULES:
        assert len(premium_eligible(price, MarketConfig(negative_price_rule=rule))) == 12
    assert set(market.vintage_rules(2024).values()) <= set(config.NEGATIVE_PRICE_RULES)


# ------------------------------------------------------------------ market value and premium
def test_market_values_use_berlin_calendar_months():
    """The quarter-hour from 23:00 UTC on 31 Jan is 00:00 in Berlin, so it is February's."""
    idx = pd.date_range("2024-01-31 22:00", periods=8, freq="15min", tz="UTC")
    price = pd.Series([10.0] * 4 + [100.0, 100.0, 40.0, 40.0], index=idx)
    feed_in = pd.DataFrame({"wind": [1.0] * 4 + [3.0, 3.0, 1.0, 1.0]}, index=idx)
    mv = market.market_values(price, feed_in, "month")
    assert list(mv.index) == ["2024-01", "2024-02"]
    assert mv.loc["2024-01", "wind"] == pytest.approx(10.0)
    assert mv.loc["2024-02", "wind"] == pytest.approx((2 * 100 * 3 + 2 * 40 * 1) / 8)


def test_annual_market_value_weights_the_whole_year_by_feed_in():
    idx = pd.date_range("2024-01-31 22:00", periods=8, freq="15min", tz="UTC")
    price = pd.Series([10.0] * 4 + [100.0, 100.0, 40.0, 40.0], index=idx)
    feed_in = pd.DataFrame({"wind": [1.0] * 4 + [3.0, 3.0, 1.0, 1.0]}, index=idx)
    mv = market.market_values(price, feed_in, "year")
    assert list(mv.index) == ["2024"]
    assert mv.loc["2024", "wind"] == pytest.approx((4 * 10 + 680) / 12)
    assert mv.loc["2024", "days"] == 2, "31 Jan and 1 Feb in Berlin"


def test_an_unknown_market_value_period_is_refused():
    idx = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    with pytest.raises(ValueError, match="period"):
        market.market_values(pd.Series(1.0, index=idx), pd.DataFrame({"wind": 1.0}, index=idx),
                             "week")


def test_premium_is_the_applicable_value_above_the_market_value_per_technology():
    """Anlage 1 Nr. 3.1.2: MP = AW - MW, and zero where that is negative."""
    idx = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    values = pd.DataFrame({"wind": [60.0], "pv": [80.0], "days": [31]}, index=["2024-01"])
    generation = {"wind": np.full(4, 30.0), "pv": np.full(4, 10.0)}
    table = market.premium_table(idx, generation, values, {"wind": 73.5, "pv": 68.5}, "month")
    assert table.loc["2024-01", "premium_wind"] == pytest.approx(13.5)
    assert table.loc["2024-01", "premium_pv"] == 0.0, "market value above the applicable value"
    assert table.loc["2024-01", "rate"] == pytest.approx(0.75 * 13.5), "wind is 3/4 of energy"
    assert market.premium_rates(idx, table, "month") == pytest.approx(np.full(4, 0.75 * 13.5))


def test_settle_takes_one_premium_rate_per_step():
    """A window that spans two months carries two premium rates."""
    run = RunConfig()
    res = {"export": np.array([10.0, 10.0, 20.0, 20.0]), "throughput": np.zeros(4),
           "chosen_curtail": np.zeros(4), "forced_curtail": np.zeros(4)}
    c = settle(res, np.full(4, 50.0), 0.25, run.plant, run.market,
               premium_rate=np.array([10.0, 10.0, 30.0, 30.0]))
    assert c["premium_revenue"] == pytest.approx((2 * 10 * 10 + 2 * 20 * 30) * 0.25)


def test_settle_requires_the_premium_rate():
    """Regression test: without a rate, settle derived one from the window's mean price."""
    run = RunConfig()
    res = {"export": np.full(4, 10.0), "throughput": np.zeros(4),
           "chosen_curtail": np.zeros(4), "forced_curtail": np.zeros(4)}
    with pytest.raises(ValueError, match="premium_rate"):
        settle(res, np.full(4, 50.0), 0.25, run.plant, run.market)


def test_the_ladder_runs_on_a_premium_that_changes_by_month():
    """Optimiser and settlement both take the per-step rate: B0 exports 30 MW throughout."""
    n = 48
    rate = np.where(np.arange(n) < 24, 10.0, 30.0)
    res = run_ladder(np.full(n, 30.0), np.zeros(n), np.full(n, 50.0), RunConfig(),
                     premium_rate=rate, include_b3=False)
    assert res["B0 no battery"]["cost"]["premium_revenue"] == pytest.approx(
        30.0 * (24 * 10.0 + 24 * 30.0) * 0.25)


# ------------------------------------------------------------------ negprice
def two_months(year: int, days: int | None = None, dt: float = 0.25,
               sunny: float = 100.0) -> pd.DataFrame:
    """January and February of `year` from Berlin midnight, cut as `load_year` cuts its frame.

    January alternates cheap windy hours (20 EUR/MWh, wind 0.8, PV 0.2) with sunny ones at
    `sunny` EUR/MWh (wind 0.2, PV 0.8). At the default 100 EUR/MWh wind's market value is 36
    and PV's 84 against a mean price of 60. February is flat at 60 EUR/MWh with both factors
    at 0.5.
    """
    jan, feb = 31 * 96, 28 * 96
    idx = pd.date_range(f"{year}-01-01", periods=jan + feb, freq="15min",
                        tz="Europe/Berlin").tz_convert("UTC")
    windy = (np.arange(jan) // 4) % 2 == 0
    df = pd.DataFrame({
        "wind_cf": np.concatenate([np.where(windy, 0.8, 0.2), np.full(feb, 0.5)]),
        "pv_cf": np.concatenate([np.where(windy, 0.2, 0.8), np.full(feb, 0.5)]),
        "price_eur_mwh": np.concatenate([np.where(windy, 20.0, sunny), np.full(feb, 60.0)]),
    }, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


def recording_ladder(calls: list):
    """Stand-in for `run_ladder` that records the premium it is handed and solves nothing."""
    def ladder(wind, pv, price, run, premium_rate, rebap=None, **kwargs):
        rate = np.broadcast_to(np.asarray(premium_rate, dtype=float), (len(price),)).copy()
        calls.append((run.market.anzulegender_wert_wind, run.market.negative_price_rule, rate))
        cost = {"net_revenue": 0.0, "chosen_curtail_mwh": 0.0}
        return {name: {"cost": cost} for name in
                ("B0 no battery", "B1 curtailment avoidance", "B2 perfect foresight")}
    return ladder


def test_negprice_pays_each_vintage_against_its_realised_market_value(monkeypatch):
    """Regression test. negprice paid max(0, AW - mean window price), one rate for both
    technologies. Per Anlage 1 Nr. 2 the older vintage is paid against January's monthly
    values (wind 36, PV 84) and the recent one against the annual values; the plant's rate
    weights the two premiums by its own generation (wind is 5/8 of it in this window).
    """
    calls = []
    monkeypatch.setattr(cli, "load_year", two_months)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder(calls))
    assert cli.main(["negprice", "--year", "2025", "--days", "2"]) == 0
    jw_wind = (1488 * 36 + 2688 * 30) / (1488 + 1344)          # January and February
    expected = {95.0: 0.625 * (95.0 - 36.0) + 0.375 * (90.0 - 84.0),
                73.5: 0.625 * (73.5 - jw_wind)}                 # PV: 68.0 - 72.6 -> zero
    assert {aw for aw, _, _ in calls} == set(expected)
    for aw, rule, rate in calls:
        assert rate == pytest.approx(np.full(192, expected[aw])), (aw, rule)


def test_negprice_pays_the_recent_vintage_at_the_default_applicable_values(monkeypatch,
                                                                         capsys):
    """Regression test: the recent vintage's PV applicable value was its wind value minus 5,
    68.5 EUR/MWh, a number with no source and above the 68.0 that MarketConfig sets and cites
    as the BNetzA ceiling. The recent vintage is the default plant, as in every other command.

    With January's sunny hours at 50 EUR/MWh, PV's annual market value falls below 68, so the
    plant's rate shows which PV applicable value was paid.
    """
    calls = []
    monkeypatch.setattr(cli, "load_year", functools.partial(two_months, sunny=50.0))
    monkeypatch.setattr(cli, "run_ladder", recording_ladder(calls))
    assert cli.main(["negprice", "--year", "2025", "--days", "2"]) == 0
    default = MarketConfig()
    jw_wind = (1488 * 26 + 1344 * 60) / (1488 + 1344)          # January 26, February 60
    jw_pv = (1488 * 44 + 1344 * 60) / (1488 + 1344)            # January 44, February 60
    expected = (0.625 * (default.anzulegender_wert_wind - jw_wind)
                + 0.375 * (default.anzulegender_wert_pv - jw_pv))
    recent = [rate for aw, _, rate in calls if aw == default.anzulegender_wert_wind]
    assert recent, "the recent vintage ran"
    for rate in recent:
        assert rate == pytest.approx(np.full(192, expected))
    assert "applicable value wind 73.5, PV 68.0" in capsys.readouterr().out


@pytest.mark.parametrize("year, eeg_2023, rules", [
    (2025, "three_hour", ["none", "six_hour", "four_hour", "three_hour", "new_2025"]),
    (2023, "four_hour", ["none", "six_hour", "four_hour", "new_2025"]),
])
def test_negprice_runs_the_rule_of_every_vintage_oldest_first(monkeypatch, capsys, year,
                                                              eeg_2023, rules):
    """Each applicable value runs under the rule of every vintage of the evaluated year, from
    the loosest to the tightest, and the output says which vintage carries which rule.

    Regression test: negprice ran none, six_hour and new_2025 whatever the year, so the
    four-hour rule of 2021-2022 and the 2023 vintage's yearly threshold never appeared. In 2023
    that threshold is four hours, the rule of 2021-2022, so one column serves both.
    """
    calls = []
    monkeypatch.setattr(cli, "load_year", two_months)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder(calls))
    assert cli.main(["negprice", "--year", str(year), "--days", "2"]) == 0
    for aw in (73.5, 95.0):
        assert [rule for a, rule, _ in calls if a == aw] == rules, aw
    lines = [ln.strip() for ln in capsys.readouterr().out.splitlines()]
    for vintage, rule in (("before 2016", "none"), ("2016-2020", "six_hour"),
                          ("2021-2022", "four_hour"), ("2023 to 24 Feb 2025", eeg_2023),
                          ("from 25 Feb 2025", "new_2025")):
        assert any(ln.startswith(vintage) and ln.endswith(rule) for ln in lines), vintage


def test_negprice_note_reports_the_window_not_a_fixed_year(monkeypatch, capsys):
    """Regression test: the note said "In 2024 ..." whatever year was run."""
    monkeypatch.setattr(cli, "load_year", two_months)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder([]))
    assert cli.main(["negprice", "--year", "2025", "--days", "2"]) == 0
    out = capsys.readouterr().out
    assert "In 2024" not in out
    assert "2025-01 (31 days): wind 36.00, PV 84.00" in out, "January's market values"
    assert "AW 73.5 (recent onshore) PV in 2025" in out, "the zero premium it gets"
    assert "AW 95.0 (older vintage) none" in out


def test_negprice_calls_its_market_values_a_proxy(monkeypatch, capsys):
    """Regression test: the output presented the computed market values as the statutory ones.

    Anlage 1 weights by the TSOs' online extrapolation, which leaves curtailment out (Nr. 5.1),
    and the premium is settled on the values they publish. The project weights by public
    generation data, so its values are a proxy and the output must say so.
    """
    monkeypatch.setattr(cli, "load_year", two_months)
    monkeypatch.setattr(cli, "run_ladder", recording_ladder([]))
    assert cli.main(["negprice", "--year", "2025", "--days", "2"]) == 0
    out = capsys.readouterr().out
    assert "market value: a proxy" in out
    assert "published values" in out, "what the premium is settled on"


# ------------------------------------------------------------------ EEG switch
def test_effective_price_carries_no_premium_when_eeg_is_off():
    """Regression test: effective_price added the premium with eeg_enabled=False, while
    `settle` paid none, so the optimiser planned on money the settlement never paid."""
    mk = MarketConfig(eeg_enabled=False, direct_marketing_fee=3.0)
    eff = market.effective_price(np.array([50.0, -10.0]), mk, premium_rate=20.0)
    assert eff == pytest.approx([47.0, -13.0]), "price minus the marketing fee only"


def test_perfect_foresight_curtails_negative_prices_when_eeg_is_off():
    """Without a premium, exporting at -10 EUR/MWh only loses money, so B2 curtails there.

    Planned on a 20 EUR/MWh premium that is never paid, B2 used to export straight through.
    """
    n = 48
    price = np.where(np.arange(n) < 24, -10.0, 50.0)
    run = RunConfig(market=MarketConfig(eeg_enabled=False, negative_price_rule="none"))
    res = run_ladder(np.full(n, 30.0), np.zeros(n), price, run, premium_rate=20.0,
                     include_b3=False)
    assert res["B2 perfect foresight"]["cost"]["chosen_curtail_mwh"] > 0.0

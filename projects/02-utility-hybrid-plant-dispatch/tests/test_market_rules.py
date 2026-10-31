"""EEG rules the settlement must follow: the negative-price rule of § 51 by vintage, and the
market premium against the technology's market value (Anlage 1).

Every case is checked against the statute's own wording; `hybrid.market` cites the paragraphs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hybrid import config, market
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
    """Six hourly steps are six hours; the same six steps as quarter-hours are only 1.5 h."""
    price = np.array([5.0] + [-1.0] * 6 + [5.0])
    six_hour = MarketConfig(negative_price_rule="six_hour")
    assert list(premium_eligible(price, six_hour, dt=1.0)) == [True] + [False] * 6 + [True]
    assert premium_eligible(price, six_hour, dt=0.25).all()


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


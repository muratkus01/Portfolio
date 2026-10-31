"""EEG rules the settlement must follow: the negative-price rule of § 51 by vintage.

Every case is checked against the statute's own wording; `hybrid.market` cites the paragraphs.
"""
from __future__ import annotations

import numpy as np
import pytest

from hybrid import config, market
from hybrid.config import MarketConfig
from hybrid.market import premium_eligible

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

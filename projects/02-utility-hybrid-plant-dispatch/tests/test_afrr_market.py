"""The aFRR leg of the settlement: capacity paid as bid per quarter-hour, and reserve not held
charged per product, as a cut of its payment plus a penalty per MWh.

Every test here fails on 2d9f26e, whose reserve module has no Payment, so this module does
not import, and whose settle takes no afrr argument.
"""
from __future__ import annotations

import datetime
import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hybrid.afrr_data import capacity_prices, parse_results
from hybrid.config import MarketConfig, PlantConfig
from hybrid.market import settle
from hybrid.plant import simulate
from hybrid.reserve import CapacityPrices, Payment, ReserveRule, paid_prices

DT = 0.25
PLANT, MARKET = PlantConfig(), MarketConfig()
RULE = ReserveRule(4.0, 1.0, 5.0)
TODAYS_KEYS = ["spot_revenue", "premium_revenue", "marketing_fee", "degradation_cost",
               "imbalance_cost", "import_cost", "net_revenue", "chosen_curtail_mwh",
               "forced_curtail_mwh", "export_mwh"]
AFRR_KEYS = ["afrr_capacity_revenue", "afrr_capacity_cut", "afrr_shortfall_cost",
             "afrr_shortfall_up_mwh", "afrr_shortfall_down_mwh", "afrr_reserve_curtail_mwh"]


def _load_fixtures():
    """tests/afrr_fixtures.py, loaded by its path: the root pyproject.toml runs the tests in
    importlib mode, which puts no test folder on sys.path."""
    path = Path(__file__).with_name("afrr_fixtures.py")
    spec = importlib.util.spec_from_file_location("afrr_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = _load_fixtures()


def _prices(first: datetime.date, days: int) -> CapacityPrices:
    """The products of `days` Berlin days from `first`, with a fixture file's prices."""
    dates = [first + datetime.timedelta(days=k) for k in range(days + 1)]
    start, end = (pd.Timestamp(day.isoformat(), tz="Europe/Berlin") for day in (dates[0],
                                                                                 dates[-1]))
    index = pd.date_range(start, end, freq="15min", inclusive="left").tz_convert("UTC")
    return capacity_prices(index, parse_results(fixtures.results_xlsx(dates[:-1])))


def _dispatch(n: int, **reserve: np.ndarray) -> dict[str, np.ndarray]:
    """A dispatch of n steps that exports and stores nothing, with the aFRR arrays given."""
    zeros = np.zeros(n)
    return {"export": zeros, "import": zeros, "throughput": zeros, "chosen_curtail": zeros,
            "forced_curtail": zeros, **reserve}


def _settle(res: dict[str, np.ndarray], afrr: Payment | None = None,
            market: MarketConfig = MARKET) -> dict[str, float]:
    """settle at a day-ahead price of 50 EUR/MWh and no premium."""
    n = len(res["export"])
    return settle(res, np.full(n, 50.0), DT, PLANT, market, premium_rate=0.0, afrr=afrr)


def test_capacity_revenue_is_price_times_mw_times_hours():
    """Capacity is paid price x awarded MW x hours, summed per quarter-hour: 10 MW in the
    08-12 POS product at 20.23 EUR/MW/h earn 809.20 EUR. The 00-04 product follows Berlin
    clock time, so it lasts 5 hours on 26 October 2025 and 3 on 30 March 2025, and the same
    10 MW earn for 20 and for 12 quarter-hours: 1,011.50 and 606.90 EUR. A 1 MW shortfall in
    one quarter-hour counts for the product's 4, 5 and 3 hours alike."""
    for first, product, hours, revenue in ((datetime.date(2025, 1, 14), 2, 4, 809.20),
                                           (datetime.date(2025, 10, 25), 6, 5, 1_011.50),
                                           (datetime.date(2025, 3, 29), 6, 3, 606.90)):
        prices = _prices(first, 2)
        n, in_product = len(prices.block), prices.block == product
        res = _dispatch(n, afrr_up=np.where(in_product, 10.0, 0.0), afrr_down=np.zeros(n))
        payment = Payment(np.full(n, 20.23), np.full(n, 99.0), prices.block)
        short = np.where(np.arange(n) == np.flatnonzero(in_product)[-1], 1.0, 0.0)

        cost = _settle(res, payment)
        unheld = _settle({**res, "afrr_deficit_up": short}, payment)

        assert np.sum(in_product) == 4 * hours, first
        assert cost["afrr_capacity_revenue"] == pytest.approx(revenue, abs=1e-9), first
        assert cost["net_revenue"] == pytest.approx(revenue, abs=1e-9), first
        assert unheld["afrr_shortfall_up_mwh"] == hours, first


def test_capacity_revenue_is_linear_in_price():
    """Twice the price earns twice the capacity revenue, in both directions."""
    rng = np.random.default_rng(2)
    prices = _prices(datetime.date(2025, 1, 14), 2)
    n, block = len(prices.block), prices.block
    up, down = rng.uniform(0, 40, 12)[block], rng.uniform(0, 40, 12)[block]
    res = _dispatch(n, afrr_up=rng.integers(0, 9, 12)[block].astype(float),
                    afrr_down=rng.integers(0, 9, 12)[block].astype(float))

    once = _settle(res, Payment(up, down, block))["afrr_capacity_revenue"]
    twice = _settle(res, Payment(2 * up, 2 * down, block))["afrr_capacity_revenue"]

    assert once > 0
    assert twice == pytest.approx(2 * once, rel=1e-12)


def test_settlement_with_afrr_sums_to_net():
    """net = spot + premium - fee - wear - imbalance - import + capacity - cut - shortfall
    cost, on a dispatch through the safety layer that holds some reserve and, starting with
    an empty battery, fails to hold some. The energy terms are those of the same dispatch
    settled without aFRR."""
    rng = np.random.default_rng(5)
    prices = _prices(datetime.date(2025, 1, 14), 2)
    n = len(prices.block)
    up = np.where(prices.block % 2 == 0, 6.0, 0.0)
    down = np.where(prices.block % 3 == 0, 4.0, 0.0)
    res = simulate(rng.uniform(0, 40, n), rng.uniform(0, 20, n), rng.uniform(-20, 20, n),
                   np.zeros(n), DT, PLANT, reserve_up=up, reserve_down=down, rule=RULE)
    price, rebap = rng.uniform(-20, 120, n), rng.uniform(-50, 200, n)
    terms = {"rebap": rebap, "schedule": res["export"] + rng.normal(0, 2, n),
             "premium_rate": 8.0}
    energy_only = {k: v for k, v in res.items() if not k.startswith(("afrr_", "reserve_"))}

    c = settle(res, price, DT, PLANT, MARKET, **terms,
               afrr=Payment(*paid_prices(prices, "average"), prices.block))
    energy = settle(energy_only, price, DT, PLANT, MARKET, **terms)

    assert list(c) == TODAYS_KEYS + AFRR_KEYS
    assert c["net_revenue"] == pytest.approx(
        c["spot_revenue"] + c["premium_revenue"] - c["marketing_fee"] - c["degradation_cost"]
        - c["imbalance_cost"] - c["import_cost"] + c["afrr_capacity_revenue"]
        - c["afrr_capacity_cut"] - c["afrr_shortfall_cost"], rel=1e-12)
    assert c["afrr_capacity_revenue"] > c["afrr_capacity_cut"] > 0
    assert c["afrr_shortfall_cost"] > 0 and c["imbalance_cost"] != 0
    assert {k: c[k] for k in TODAYS_KEYS if k != "net_revenue"} == {
        k: energy[k] for k in TODAYS_KEYS if k != "net_revenue"}


def test_reserve_not_held_is_charged_per_product_with_its_payment_cut():
    """A product is sold for all its hours: a 2 MW shortfall in one quarter-hour of a 4-hour
    POS product cuts its payment by c x 2 MW x 4 h, 15 x 8 = 120 EUR, and costs the penalty on
    the same 8 MWh, 80,000 EUR. A smaller shortfall elsewhere in the product adds nothing; a
    3 MW one in a NEG product paid 7 EUR/MW/h adds its own 12 MWh, 84 EUR of cut and
    120,000 EUR of penalty. The 5 MW that positive reserve's headroom curtailed for an hour
    are reported as 5 MWh and charged nothing here: their energy is missing from the
    export the energy terms are paid on. The penalty is the setting's: at 20,000 EUR/MWh the
    same 20 MWh cost twice as much, and the cut and the MWh do not change."""
    n, block = 48, np.repeat(np.arange(3), 16)
    short_up, short_down = np.zeros(n), np.zeros(n)
    short_up[[5, 9]] = 2.0, 1.0                     # both in product 0
    short_down[20] = 3.0                            # in product 1
    res = _dispatch(n, afrr_up=np.full(n, 6.0), afrr_down=np.full(n, 4.0),
                    afrr_deficit_up=short_up, afrr_deficit_down=short_down,
                    reserve_curtail=np.where(np.arange(n) < 4, 5.0, 0.0))
    payment = Payment(np.full(n, 15.0), np.full(n, 7.0), block)

    c = _settle(res, payment)
    dearer = _settle(res, payment, replace(MARKET, afrr_shortfall_penalty_eur_mwh=20_000.0))

    assert (c["afrr_shortfall_up_mwh"], c["afrr_shortfall_down_mwh"]) == (8.0, 12.0)
    assert c["afrr_capacity_cut"] == pytest.approx(120.0 + 84.0)
    assert c["afrr_shortfall_cost"] == pytest.approx(10_000 * 20.0)
    assert c["afrr_capacity_revenue"] == pytest.approx((15.0 * 6.0 + 7.0 * 4.0) * 12.0)
    assert c["afrr_reserve_curtail_mwh"] == 5.0
    assert dearer["afrr_shortfall_cost"] == pytest.approx(20_000 * 20.0)
    unchanged = ("afrr_capacity_cut", "afrr_shortfall_up_mwh", "afrr_shortfall_down_mwh")
    assert [dearer[k] for k in unchanged] == [c[k] for k in unchanged]


def test_b3_is_paid_its_bid_only_where_it_was_accepted():
    """Pay-as-bid: with B3's payment, paid_prices(prices, "bid"), a product whose bid lay
    above the realised marginal pays nothing even with MW in it, and an accepted one pays its
    bid, not the average. 3 MW over two 1-hour products, bid 10 EUR/MW/h, marginals 20 and 9:
    30 EUR, where the average, 12 EUR/MW/h, would have paid 72."""
    zeros = np.zeros(8)
    prices = CapacityPrices(block=np.repeat([0, 1], 4), day=np.ones(8, dtype=int),
                            gate=zeros.astype(bool), award=zeros.astype(bool),
                            average_up=np.full(8, 12.0), average_down=np.full(8, 6.0),
                            marginal_up=np.repeat([20.0, 9.0], 4),
                            marginal_down=np.full(8, 9.0),
                            bid_up=np.full(8, 10.0), bid_down=np.full(8, 5.0))
    res = _dispatch(8, afrr_up=np.full(8, 3.0), afrr_down=zeros)

    bid = _settle(res, Payment(*paid_prices(prices, "bid"), prices.block))
    average = _settle(res, Payment(*paid_prices(prices, "average"), prices.block))

    assert bid["afrr_capacity_revenue"] == pytest.approx(10.0 * 3.0 * 1.0)
    assert average["afrr_capacity_revenue"] == pytest.approx(12.0 * 3.0 * 2.0)


def test_off_settlement_keeps_todays_keys():
    """Without afrr settle returns today's keys in today's order, and the same values as
    when the argument is left out; with it, the six aFRR terms follow today's keys and a
    dispatch that holds no reserve, as B0's and B1's, earns and pays 0 on them."""
    rng = np.random.default_rng(9)
    n = 96
    res = simulate(rng.uniform(0, 40, n), rng.uniform(0, 20, n), rng.uniform(-20, 20, n),
                   np.zeros(n), DT, PLANT)
    price = rng.uniform(-20, 120, n)

    plain = settle(res, price, DT, PLANT, MARKET, premium_rate=8.0)
    off = settle(res, price, DT, PLANT, MARKET, premium_rate=8.0, afrr=None)
    held_none = settle(res, price, DT, PLANT, MARKET, premium_rate=8.0,
                       afrr=Payment(np.full(n, 20.0), np.full(n, 20.0), np.arange(n) // 16))

    assert list(plain) == TODAYS_KEYS and off == plain
    assert list(held_none) == TODAYS_KEYS + AFRR_KEYS
    assert {k: held_none[k] for k in TODAYS_KEYS} == plain
    assert [held_none[k] for k in AFRR_KEYS] == [0.0] * 6


def test_reserve_held_is_never_settled_without_its_payment():
    """A dispatch that holds reservations is refused without afrr=, so that its capacity and
    its reserve not held cannot drop out of a settlement unnoticed. That includes a dispatch
    whose reservations are all 0 MW: every rung of an aFRR run is settled with its six aFRR
    terms, so that no row of its table lacks them."""
    for up in (np.ones(4), np.zeros(4)):
        res = _dispatch(4, afrr_up=up, afrr_down=np.zeros(4))
        with pytest.raises(ValueError, match="holds aFRR reservations"):
            _settle(res)


def test_settle_refuses_a_payment_it_cannot_read():
    """afrr= holds one finite price of at least 0 EUR/MW/h per step and direction, and one
    product per step; anything else is refused, not broadcast or summed into NaN."""
    n, ones = 4, np.ones(4)
    res = _dispatch(n, afrr_up=ones, afrr_down=ones)
    for afrr in (Payment(ones[:3], ones, np.arange(n)), Payment(ones, -ones, np.arange(n)),
                 Payment(np.full(n, np.nan), ones, np.arange(n)), Payment(ones, ones, 0),
                 Payment(5.0, ones, np.arange(n))):
        with pytest.raises(ValueError, match="afrr holds one finite price"):
            _settle(res, afrr)

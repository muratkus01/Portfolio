"""Accounting of the benchmark ladder: what each rung is credited with, and the invariant."""
from __future__ import annotations

import numpy as np
import pytest

import hybrid.baselines as baselines
from hybrid.baselines import run_ladder
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.plant import simulate


def window(n: int, seed: int = 0):
    """Wind and PV in MW and a day-ahead price in EUR/MWh, starting at midnight."""
    rng = np.random.default_rng(seed)
    h = np.arange(n) * 0.25 % 24
    wind = np.clip(28 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 30 * (pv / 30) \
        + 15 * rng.standard_normal(n)
    return wind, pv, price


# ------------------------------------------------------------------ invariant tolerance
def test_perfect_forecasts_do_not_trip_the_ladder_invariant():
    """Regression: with forecast_sigma = 0, B3 beat B2 by solver rounding and the run raised.

    PuLP reads CBC's solution with about eight significant digits. B2 executes one plan, so
    its rounding residue stays in the result; B3 re-solves from the true state every step and
    does not. The gap is a few 1e-9 of the objective (0.0013 EUR on a 210,894 EUR week of 2024
    data), which passed the old absolute 1e-3 EUR tolerance once the objective was large. The
    plant is scaled 20x so that half a day earns what a published 20-day window earns.
    """
    k = 20.0
    plant = PlantConfig(wind_mw=50 * k, pv_mw=30 * k, bess_mw=20 * k, bess_mwh=40 * k,
                        conn_mw=80 * k)
    run = RunConfig(dt=0.25, horizon_steps=48, forecast_sigma=0.0, plant=plant)
    wind, pv, price = window(48, seed=1)

    res = run_ladder(wind * k, pv * k, price, run, premium_rate=8.0, include_b3=True)

    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    assert res["B3 rolling MPC"]["cost"]["net_revenue"] == pytest.approx(b2, rel=1e-6)


def test_a_genuine_ladder_violation_still_raises(monkeypatch):
    """The relative tolerance must not hide a real defect.

    A "perfect foresight" rung that throws away 0.1 % of the generation loses to the
    no-battery plant by about a thousand times the tolerance, so the run must stop.
    """
    run = RunConfig(dt=0.25)
    wind, pv, price = window(16)

    def wasteful_b2(wind, pv, eff_price, run, export_cap=None):
        n = len(wind)
        return simulate(wind, pv, np.zeros(n), np.full(n, 0.001), run.dt, run.plant,
                        export_cap=export_cap)

    monkeypatch.setattr(baselines, "b2_perfect", wasteful_b2)
    with pytest.raises(AssertionError, match="ladder invariant violated"):
        run_ladder(wind, pv, price, run, premium_rate=8.0, include_b3=False)


# ------------------------------------------------------------------ imbalance
@pytest.mark.parametrize("committed_mw, sign", [(65.0, +1), (0.0, -1)],
                         ids=["promised more than the connection carries", "promised nothing"])
def test_a_deviation_from_the_committed_schedule_is_settled_at_rebap(committed_mw, sign):
    """Regression: run_ladder dropped the schedule whenever reBAP was given, so the imbalance
    cost was 0 in every run.

    reBAP sits 70 EUR/MWh above spot (the system is short). The settlement pays spot on all
    delivered energy, so imbalance_cost is the deviation priced at the spread, reBAP minus
    spot: a shortfall costs 70 EUR/MWh, and a surplus earns 70 EUR/MWh more than spot.
    """
    run = RunConfig(dt=0.25)
    wind, pv, price = window(16)
    rebap = price + 70.0
    schedule = np.full(16, committed_mw)

    res = run_ladder(wind, pv, price, run, premium_rate=8.0, rebap=rebap, schedule=schedule,
                     include_b3=False)

    for name, r in res.items():
        deviation = r["export"] - schedule
        assert r["cost"]["imbalance_cost"] == pytest.approx(-np.sum(deviation * 70.0) * run.dt)
        assert np.sign(r["cost"]["imbalance_cost"]) == sign, name


def test_single_price_settlement_pays_the_deviation_once():
    """Regression: the deviation was paid twice, at spot and again at reBAP.

    Committed 10 MW, delivered 12 MW for an hour, spot 50 and reBAP 80 EUR/MWh. Single-price
    settlement pays the 10 MWh committed at spot and the 2 MWh surplus at reBAP: 660 EUR, not
    760. With no fee, no premium and a flat price the battery stays idle, so every rung
    delivers the same 12 MW.
    """
    run = RunConfig(dt=0.25, market=MarketConfig(direct_marketing_fee=0.0))
    n = 4

    res = run_ladder(np.full(n, 12.0), np.zeros(n), np.full(n, 50.0), run, premium_rate=0.0,
                     rebap=np.full(n, 80.0), include_b3=False, schedule=np.full(n, 10.0))

    nets = {name: r["cost"]["net_revenue"] for name, r in res.items()}
    assert nets == pytest.approx(dict.fromkeys(nets, 10 * 50.0 + 2 * 80.0))


def test_imbalance_luck_does_not_trip_the_ladder_invariant():
    """B2 is the ceiling of what it optimises, and imbalance lies outside that objective.

    At a scarcity reBAP of 1,000 EUR/MWh with nothing committed, every delivered MWh earns
    reBAP, so the rung that exports most wins on imbalance alone. That is luck, not a defect:
    the run must not stop, and B2 must still lead before imbalance.
    """
    run = RunConfig(dt=0.25, plant=PlantConfig(conn_mw=40.0))
    wind, pv, price = window(96, seed=2)
    n = len(price)

    res = run_ladder(wind, pv, price, run, premium_rate=8.0, rebap=np.full(n, 1000.0),
                     schedule=np.zeros(n), include_b3=False)

    net = {name: r["cost"]["net_revenue"] for name, r in res.items()}
    before = {name: r["cost"]["net_revenue"] + r["cost"]["imbalance_cost"]
              for name, r in res.items()}
    assert max(net, key=net.get) != "B2 perfect foresight"
    assert max(before, key=before.get) == "B2 perfect foresight"


# ------------------------------------------------------------------ grid imports
def test_grid_imports_are_paid_at_the_day_ahead_price(monkeypatch):
    """Regression: with grid charging allowed, the plant imported energy for free.

    No rung charges from the grid today (the LP cannot import), so B1 is replaced by a
    controller that buys an hour of full-power charging and sells it back. Bought and sold at
    one flat price, that round trip must lose money; with free imports it made money from
    nothing and out-earned perfect foresight.
    """
    plant = PlantConfig(grid_charging_allowed=True)
    run = RunConfig(dt=0.25, plant=plant)
    n = 8
    nothing = np.zeros(n)
    price = np.full(n, 60.0)
    charge_then_sell = np.r_[np.full(4, -plant.bess_mw), np.full(4, plant.bess_mw)]

    def grid_trader(wind, pv, run, export_cap=None):
        return simulate(wind, pv, charge_then_sell, np.zeros(n), run.dt, run.plant,
                        export_cap=export_cap)

    monkeypatch.setattr(baselines, "b1_curtailment_avoidance", grid_trader)
    res = run_ladder(nothing, nothing, price, run, premium_rate=0.0, include_b3=False)

    trader = res["B1 curtailment avoidance"]
    bought_mwh = np.sum(trader["import"]) * run.dt
    assert bought_mwh > 0
    assert trader["cost"]["import_cost"] == pytest.approx(bought_mwh * 60.0)
    assert trader["cost"]["net_revenue"] < 0


# ------------------------------------------------------------------ initial stored energy
def test_no_rung_earns_from_energy_it_never_bought():
    """Regression: every battery rung started half full and sold that energy as revenue.

    No rung has an end-of-window state-of-charge condition, so the 18 MWh above the minimum
    that the battery held at the start were sold and counted as battery value (B2 minus B0).
    With no generation and no grid charging there is nothing to store, so every rung, and
    therefore the battery, must be worth 0.
    """
    run = RunConfig(dt=0.25)
    n = 8
    nothing = np.zeros(n)

    res = run_ladder(nothing, nothing, np.full(n, 60.0), run, premium_rate=0.0,
                     include_b3=True)

    for name, r in res.items():
        assert r["cost"]["net_revenue"] == pytest.approx(0.0, abs=1e-6), name


def test_the_battery_starts_at_its_minimum_whatever_the_minimum():
    """Starting empty must follow the minimum, or a higher minimum starts below it."""
    assert PlantConfig().soc_init == pytest.approx(PlantConfig().soc_min)
    assert PlantConfig(soc_min_frac=0.10).soc_init == pytest.approx(4.0)

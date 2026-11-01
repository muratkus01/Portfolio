"""Accounting of the benchmark ladder: what each rung is credited with, and the invariant."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pulp
import pytest

import hybrid.baselines as baselines
from hybrid.baselines import (b0_no_battery, b1_curtailment_avoidance, b2_perfect, b3_rolling,
                              make_forecasts, run_ladder)
from hybrid.config import MarketConfig, PlantConfig, RunConfig
from hybrid.market import effective_price, premium_eligible, settle
from hybrid.plant import simulate

# Every output of the rungs on the LP's stand-in, recorded at a802748, the last commit before
# the aFRR build (test_the_rungs_reproduce_their_golden_runs_on_a_stand_in_solver).
GOLDENS = Path(__file__).with_name("goldens_a802748_standin.json")


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
    does not. The gap is a few 1e-9 of the objective, which exceeded the old absolute 1e-3 EUR
    tolerance once the objective was large. The plant is scaled 20x so that half a day earns
    about as much as a published 20-day window.
    """
    k = 20.0
    plant = PlantConfig(wind_mw=50 * k, pv_mw=30 * k, bess_mw=20 * k, bess_mwh=40 * k,
                        conn_mw=80 * k)
    run = RunConfig(dt=0.25, horizon_steps=48, forecast_sigma=0.0, plant=plant)
    wind, pv, price = window(48, seed=1)

    res = run_ladder(wind * k, pv * k, price, run, premium_rate=8.0, include_b3=True)

    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    assert res["B3 rolling MPC"]["cost"]["net_revenue"] == pytest.approx(b2, rel=1e-6)


def test_a_rounding_size_gain_over_b2_does_not_trip_the_ladder_invariant(monkeypatch):
    """The tolerance itself, whatever the CBC build rounds to.

    B3 is replaced by B2's own dispatch with 1e-8 more export. On this window that gain is
    above the old absolute 1e-3 EUR, which stopped the run, and far below 1e-6 of B2.
    """
    k = 20.0
    plant = PlantConfig(wind_mw=50 * k, pv_mw=30 * k, bess_mw=20 * k, bess_mwh=40 * k,
                        conn_mw=80 * k)
    run = RunConfig(dt=0.25, horizon_steps=48, plant=plant)
    wind, pv, price = window(48, seed=1)

    def b2_with_a_little_more_export(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run,
                                     export_cap=None, progress=False, premium=None):
        res = baselines.b2_perfect(wind, pv, eff_price, run, export_cap)
        return {**res, "export": res["export"] * (1 + 1e-8)}

    monkeypatch.setattr(baselines, "b3_rolling", b2_with_a_little_more_export)
    res = run_ladder(wind * k, pv * k, price, run, premium_rate=8.0, include_b3=True)

    b2 = res["B2 perfect foresight"]["cost"]["net_revenue"]
    gain = res["B3 rolling MPC"]["cost"]["net_revenue"] - b2
    assert 1e-3 < gain < 1e-6 * b2


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


def test_settle_charges_grid_imports_at_the_day_ahead_price():
    """Regression test: settle priced grid imports at 0, so its direct callers got them free.

    The ladder charged them in a wrapper of its own; settle now charges them, in one place.
    8 MW bought for an hour at 60 EUR/MWh cost 480 EUR, with nothing sold.
    """
    run = RunConfig(market=MarketConfig(direct_marketing_fee=0.0))
    n = 4
    res = {"export": np.zeros(n), "import": np.full(n, 8.0), "throughput": np.zeros(n),
           "chosen_curtail": np.zeros(n), "forced_curtail": np.zeros(n)}

    cost = settle(res, np.full(n, 60.0), 0.25, run.plant, run.market, premium_rate=0.0)

    assert cost["net_revenue"] == pytest.approx(-480.0)
    assert cost["import_cost"] == pytest.approx(480.0)


def test_the_market_premium_is_not_paid_on_grid_energy(monkeypatch):
    """Regression: energy bought from the grid and sold again earned the market premium.

    The premium follows renewable energy into the battery (§ 19 (3) EEG 2023); grid energy
    carries none. The stand-in for B1 charges at 20 MW beside 10 MW of wind for an hour, so
    half of what it stores comes from the grid, and sells it all in the next hour. The ladder
    cannot tell the two apart in the battery, so it must refuse the rung rather than pay the
    premium on the grid share. The rung stays below B2, so the invariant did not catch it.
    """
    plant = PlantConfig(grid_charging_allowed=True)
    run = RunConfig(dt=0.25, plant=plant)
    n = 8
    charge_then_sell = np.r_[np.full(4, -plant.bess_mw), np.full(4, plant.bess_mw)]

    def grid_trader(wind, pv, run, export_cap=None):
        return simulate(wind, pv, charge_then_sell, np.zeros(n), run.dt, run.plant,
                        export_cap=export_cap)

    monkeypatch.setattr(baselines, "b1_curtailment_avoidance", grid_trader)
    with pytest.raises(NotImplementedError, match="from the grid"):
        run_ladder(np.full(n, 10.0), np.zeros(n), np.full(n, 60.0), run, premium_rate=10.0,
                   include_b3=False)


def test_solver_residue_is_not_taken_for_grid_energy():
    """The refusal must not fire on rungs that never buy from the grid.

    With grid charging allowed, LP rounding can leave B2 importing a tiny share of what it
    charges. That is residue, not trading, so the run must settle as usual.
    """
    run = RunConfig(dt=0.25, plant=PlantConfig(conn_mw=40.0, grid_charging_allowed=True))
    wind, pv, price = window(96)

    res = run_ladder(wind, pv, price, run, premium_rate=8.0, include_b3=False)

    b2 = res["B2 perfect foresight"]
    assert np.sum(b2["import"]) <= 1e-6 * np.sum(b2["p_ch"])


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

    nets = {name: r["cost"]["net_revenue"] for name, r in res.items()}
    assert nets == pytest.approx(dict.fromkeys(nets, 0.0), abs=1e-6)


def test_the_battery_starts_at_its_minimum_whatever_the_minimum():
    """Starting empty must follow the minimum, or a higher minimum starts below it."""
    assert PlantConfig().soc_init == pytest.approx(PlantConfig().soc_min)
    assert PlantConfig(soc_min_frac=0.10).soc_init == pytest.approx(4.0)


@pytest.mark.parametrize("frac", [0.0, 0.99], ids=["below the minimum", "above the maximum"])
def test_a_start_outside_the_battery_limits_is_rejected(frac):
    """Regression: a start below the minimum made the battery charge from the grid.

    The safety layer pulled the battery up to its minimum, and because that bound beat the
    grid-charging bound, it drew the energy from the grid even with grid charging forbidden.
    A start outside the limits is a state the plant cannot be in, so it must be refused.
    """
    with pytest.raises(ValueError, match="soc_init_frac"):
        PlantConfig(soc_init_frac=frac)


# ------------------------------------------------------------------ pins: aFRR off
# The aFRR build adds a capacity leg to these rungs behind a switch that is off by default.
# With it off, they must do exactly what they did before it. These pins hold that on every
# platform without CBC: the LP the solver receives, and every output of the rungs on a
# deterministic stand-in for the solver.
def _frozen_lp(wind, pv, eff_price, dt, run, soc0, terminal_price=0.0,
               export_cap=None) -> pulp.LpProblem:
    """A frozen copy of the model `baselines.solve_window` built at a802748, up to the solve.

    Never edit it to match the code: a difference means that the energy-only LP changed.
    """
    cfg = run.plant
    n = len(wind)
    m = pulp.LpProblem("hybrid", pulp.LpMaximize)

    gen = np.asarray(wind, float) + np.asarray(pv, float)
    cap = np.full(n, cfg.conn_mw) if export_cap is None else np.minimum(export_cap, cfg.conn_mw)

    g = pulp.LpVariable.dicts("g", range(n), lowBound=0)
    ch = pulp.LpVariable.dicts("ch", range(n), lowBound=0, upBound=cfg.bess_mw)
    dis = pulp.LpVariable.dicts("dis", range(n), lowBound=0, upBound=cfg.bess_mw)
    soc = pulp.LpVariable.dicts("soc", range(n), lowBound=cfg.soc_min, upBound=cfg.soc_max)
    exp = pulp.LpVariable.dicts("exp", range(n), lowBound=0)

    m += (pulp.lpSum(exp[t] * float(eff_price[t]) * dt for t in range(n))
          - pulp.lpSum((ch[t] + dis[t]) * dt * cfg.c_deg_eur_mwh for t in range(n))
          + terminal_price * soc[n - 1])

    for t in range(n):
        prev = soc0 if t == 0 else soc[t - 1]
        m += soc[t] == prev + (cfg.eta_c * ch[t] - dis[t] / cfg.eta_d) * dt
        m += g[t] <= float(gen[t])
        m += exp[t] == g[t] + dis[t] - ch[t]
        m += exp[t] <= float(cap[t])
        if not cfg.grid_charging_allowed:
            m += ch[t] <= g[t]
    return m


def _lp_terms(m: pulp.LpProblem) -> dict:
    """What CBC receives of a model: its name and sense, every variable with its bounds and
    category, every constraint in order with its name, sense, coefficients and constant, and
    the objective's coefficients and constant."""
    def coefficients(expression) -> list:
        return sorted((variable.name, value) for variable, value in expression.items())

    return {"problem": (m.name, m.sense),
            "variables": [(v.name, v.lowBound, v.upBound, v.cat) for v in m.variables()],
            "constraints": [(name, c.sense, c.constant, coefficients(c))
                            for name, c in m.constraints.items()],
            "objective": (m.objective.constant, coefficients(m.objective))}


def test_the_off_path_builds_todays_lp(monkeypatch):
    """A pin: with aFRR off, solve_window hands the solver the LP it built at a802748.

    The aFRR build moves the model into a builder of its own and adds reserve terms behind a
    switch. A recorder takes the solver's place, so the check needs no CBC and holds on every
    platform. It covers the export cap, grid charging both ways and a terminal price.
    """
    built = []

    def record(problem, *args, **kwargs):
        built.append(problem)
        return problem.status

    monkeypatch.setattr(pulp.LpProblem, "solve", record)
    wind, pv, price = window(8, seed=3)
    eff = price - 60.0
    order = np.r_[np.full(4, 70.0), np.full(4, 25.0)]   # above, then below, the 60 MW connection
    for cap, grid, terminal in itertools.product((None, order), (False, True), (0.0, 41.5)):
        run = RunConfig(plant=PlantConfig(grid_charging_allowed=grid))
        built.clear()
        baselines.solve_window(wind, pv, eff, run.dt, run, 12.0, terminal_price=terminal,
                               export_cap=cap)
        case = f"export cap {cap is not None}, grid charging {grid}, terminal price {terminal}"
        assert len(built) == 1, case
        frozen = _frozen_lp(wind, pv, eff, run.dt, run, 12.0, terminal, cap)
        assert _lp_terms(built[0]) == _lp_terms(frozen), case


def _stand_in_solve_window(wind, pv, eff_price, dt, run, soc0, terminal_price=0.0,
                           export_cap=None, msg=False):
    """A deterministic stand-in for the LP, in numpy, with solve_window's signature.

    It discharges above the window's median price and charges below it, the harder the fuller
    the battery starts; it curtails at negative prices and where the connection binds. It
    ignores every limit, so the safety layer has to clip its plans, and each argument the
    rungs pass moves the plan, so the goldens pin what they pass as well. It uses arithmetic
    and a median only, so its plans are the same on every platform.
    """
    cfg = run.plant
    price = np.asarray(eff_price, dtype=float)
    gen = np.asarray(wind, dtype=float) + np.asarray(pv, dtype=float)
    cap = (np.full(len(price), cfg.conn_mw) if export_cap is None
           else np.minimum(export_cap, cfg.conn_mw))
    fill = (soc0 - cfg.soc_min) / (cfg.soc_max - cfg.soc_min)
    p_bat = (price - np.median(price) - 0.1 * terminal_price) * (0.4 + fill) / (4.0 * dt)
    curt = np.where(price < 0.0, 0.5, 0.0) + np.where(gen > cap, 0.25, 0.0)
    return p_bat, curt


def _as_json(result: dict) -> dict:
    """A rung's outputs as JSON values, without its solve times, which are measured."""
    def value(v):
        if isinstance(v, np.ndarray):
            return v.tolist()
        return dict(v) if isinstance(v, dict) else float(v)

    return {key: value(v) for key, v in result.items() if key != "solve_time_s"}


def _stand_in_runs() -> dict[str, dict]:
    """B0 to B3 and run_ladder on the stand-in, as JSON values per run.

    One day of `window` from midnight, shifted to give negative prices, at a 40 MW connection
    so that it binds. run_ladder runs every rung with B3 re-planning every step, a premium
    that changes within the window, reBAP and a schedule. The rungs then run alone under a
    redispatch order, B3 with a terminal value and a re-plan every 4 steps.
    """
    wind, pv, price = window(96, seed=2)
    price = price - 60.0
    n = len(price)
    run = RunConfig(dt=0.25, horizon_steps=16, plant=PlantConfig(conn_mw=40.0))
    premium = np.r_[np.full(48, 8.0), np.full(48, 11.0)]
    rebap = price + np.where(np.arange(n) % 3 == 0, 40.0, -20.0)
    ladder = run_ladder(wind, pv, price, run, premium_rate=premium, rebap=rebap,
                        schedule=np.full(n, 30.0), b3_premium_rate=np.full(n, 9.5),
                        include_b2_no_battery=True)
    runs = {f"ladder: {name}": r for name, r in ladder.items()}
    order = np.r_[np.full(40, 40.0), np.full(16, 22.0), np.full(40, 40.0)]
    eff = effective_price(price, run.market, 8.0, run.dt)
    runs["B0, redispatch"] = b0_no_battery(wind, pv, run, order)
    runs["B1, redispatch"] = b1_curtailment_avoidance(wind, pv, run, order)
    runs["B2, redispatch"] = b2_perfect(wind, pv, eff, run, order)
    forecasts = make_forecasts(wind, pv, eff, run, np.random.default_rng(7))
    eligible = premium_eligible(price, run.market, run.dt)
    runs["B3 every 4 steps, terminal value, redispatch"] = b3_rolling(
        wind, pv, eff, *forecasts, run.with_(terminal_value=True), order, resolve_every=4,
        premium=(np.full(n, 8.0), eligible))
    return {name: _as_json(r) for name, r in runs.items()}


def test_the_rungs_reproduce_their_golden_runs_on_a_stand_in_solver(monkeypatch):
    """A pin: with aFRR off, the rungs' loops, the safety layer and the settlement give what
    they gave at a802748, output for output.

    CBC is replaced by `_stand_in_solve_window`, so the pin needs no solver and holds on every
    platform. The goldens were recorded at a802748 with this stand-in, as json.dumps of
    `_stand_in_runs()`; never re-record them to make this test pass. Its plans are not
    optimal, so B2 need not lead the ladder: the invariant is switched off here, and the
    other tests of this module keep it.
    """
    monkeypatch.setattr(baselines, "solve_window", _stand_in_solve_window)
    monkeypatch.setattr(baselines, "ladder_tolerance", lambda b2_revenue: float("inf"))
    golden = json.loads(GOLDENS.read_text())

    runs = _stand_in_runs()

    assert list(runs) == list(golden)
    for name, outputs in runs.items():
        assert list(outputs) == list(golden[name]), name
        for key, value in outputs.items():
            assert value == pytest.approx(golden[name][key], rel=1e-12), f"{name}: {key}"

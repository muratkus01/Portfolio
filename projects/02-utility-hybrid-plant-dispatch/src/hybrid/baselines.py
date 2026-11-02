"""Benchmark ladder for the hybrid plant.

B0  unhybridised reference   wind + PV marketed separately, no battery, no curtailment choice
B1  commercial practice      battery on a curtailment-avoidance rule, no price awareness
B2  perfect foresight        LP over the whole period, knows prices and generation exactly
B3  rolling-horizon MPC      the deployable classical optimum, on forecasts

"B2 no battery", B2 with the battery taken out, splits B2's gain over B0 into what curtailing
at low prices earns, which needs no battery, and what the battery itself adds.

With aFRR capacity prices (`run_ladder(afrr=...)`), B2 also reserves aFRR capacity, and B3
bids it at each day's gate (`afrr_rungs`); B0, B1 and "B2 no battery" never do.
"""
from __future__ import annotations

import time
from dataclasses import replace

import numpy as np
import pulp

from .config import MarketConfig, PlantConfig, RunConfig
from .market import effective_price, premium_eligible, settle
from .plant import battery_interval, simulate
from .reserve import PLAN_MARGIN, CapacityPrices, Payment, paid_prices

# Tolerance of the ladder invariant. PuLP reads CBC's solution with about eight significant
# digits, so a rung that re-solves every step (B3) can beat the single plan of B2 by rounding
# alone under perfect forecasts: by a few 1e-9 of the objective, as a test in
# tests/test_ladder_accounting.py shows. The tolerance therefore scales with the objective;
# the floor covers objectives near zero.
_INVARIANT_REL_TOL = 1e-6
_INVARIANT_ABS_TOL = 1e-3          # EUR
# Share of the charged energy that may come from the grid before it counts as bought. With
# grid charging allowed, LP rounding alone imports a tiny share of the charged energy, at any
# window length and plant size; an absolute threshold would refuse that residue on a long
# enough window or a large enough plant.
_GRID_SHARE_TOL = 1e-6


def ladder_tolerance(b2_revenue: float) -> float:
    """How far a rung may out-earn B2 before the ladder invariant counts it as a defect.

    b2_revenue: B2's revenue before imbalance, EUR, e.g. 250_000.0.
    Returns the tolerance in EUR: _INVARIANT_REL_TOL of |b2_revenue|, and at least
    _INVARIANT_ABS_TOL.
    """
    return max(_INVARIANT_ABS_TOL, _INVARIANT_REL_TOL * abs(b2_revenue))


# ------------------------------------------------------------------ B0 / B1
def _without_battery(plant: PlantConfig) -> PlantConfig:
    """The plant with its battery taken out: a 0 MW, 1e-6 MWh stand-in, started empty.

    plant: the plant as built. Returns a copy for the rungs without a battery and their
    feasibility check.
    """
    return replace(plant, bess_mw=0.0, bess_mwh=1e-6, soc_init_frac=None)


def rung_plant(name: str, plant: PlantConfig) -> PlantConfig:
    """The plant a rung runs on: without its battery for "B0 no battery" and "B2 no battery"
    (`_without_battery`), as built for every other rung.

    name: the rung, e.g. "B2 no battery". plant: the plant as built.
    """
    return _without_battery(plant) if "no battery" in name else plant


def b0_no_battery(wind: np.ndarray, pv: np.ndarray, run: RunConfig,
                  export_cap: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """The counterfactual: same generation, no storage, curtail only when forced.

    This is what the assets earn WITHOUT hybridisation, and is the commercially relevant
    reference for the question "is the battery worth building?".
    """
    n = len(wind)
    cfg_nobess = _without_battery(run.plant)
    # Start the 1e-6 MWh stand-in at its own minimum: from 0, below that minimum, the safety
    # layer forced it to charge, which drew a few 1e-8 MWh from the grid.
    return simulate(wind, pv, np.zeros(n), np.zeros(n), run.dt, cfg_nobess,
                    soc0=cfg_nobess.soc_min, export_cap=export_cap)


def b1_curtailment_avoidance(wind: np.ndarray, pv: np.ndarray, run: RunConfig,
                             export_cap: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Charge whenever generation would otherwise be spilled; discharge whenever there is
    headroom under the connection limit. No price awareness at all.

    This is close to how such plants are actually run today, and it is the validation gate.
    """
    cfg = run.plant
    n = len(wind)
    p_bat = np.zeros(n)
    soc = cfg.soc_init
    for t in range(n):
        avail = wind[t] + pv[t]
        cap = cfg.conn_mw if export_cap is None else min(cfg.conn_mw, export_cap[t])
        lo, hi = battery_interval(soc, run.dt, cfg)
        if avail > cap:                       # would spill: absorb what we can
            p_bat[t] = max(lo, -(avail - cap))
        else:                                 # headroom available: push stored energy out
            p_bat[t] = min(hi, cap - avail)
        from .plant import soc_next
        soc = soc_next(soc, p_bat[t], run.dt, cfg)
    return simulate(wind, pv, p_bat, np.zeros(n), run.dt, cfg, export_cap=export_cap)


# ------------------------------------------------------------------ LP core
def solve_window(wind: np.ndarray, pv: np.ndarray, eff_price: np.ndarray, dt: float,
                 run: RunConfig, soc0: float, terminal_price: float = 0.0,
                 export_cap: np.ndarray | None = None,
                 msg: bool = False) -> tuple[np.ndarray, np.ndarray] | None:
    """Maximise the value of exported energy. Returns (battery power, curtailment fraction).

    Curtailment is a free decision variable: with the negative-price rule active the
    effective price can be negative, and curtailing then is strictly optimal.
    """
    m, v = _energy_lp(wind, pv, eff_price, dt, run, soc0, terminal_price, export_cap)
    m.solve(pulp.PULP_CBC_CMD(msg=msg))
    if pulp.LpStatus[m.status] != "Optimal":
        return None
    return _read_plan(v, wind, pv)


def _export_limit(n: int, cfg: PlantConfig, export_cap: np.ndarray | None) -> np.ndarray:
    """The export limit of each of n steps, MW: the connection, or a redispatch order below it.

    cfg: the plant. export_cap: the order's limit per step, MW, or None for the connection.
    """
    return np.full(n, cfg.conn_mw) if export_cap is None else np.minimum(export_cap, cfg.conn_mw)


def _energy_lp(wind: np.ndarray, pv: np.ndarray, eff_price: np.ndarray, dt: float,
               run: RunConfig, soc0: float, terminal_price: float = 0.0,
               export_cap: np.ndarray | None = None) -> tuple[pulp.LpProblem, dict]:
    """The energy-only window LP of `solve_window`, built and not solved.

    The aFRR rungs add their reserve terms to this model (`reserve.add_reserve_terms`), so
    that with aFRR off the solver receives exactly the model it received before the build.
    Arguments as for `solve_window`. Returns (the model, its variables by name: "g", "ch",
    "dis", "soc" and "exp", each a dict by step; soc[t] is the energy stored at the end of
    step t).
    """
    cfg = run.plant
    n = len(wind)
    m = pulp.LpProblem("hybrid", pulp.LpMaximize)

    gen = np.asarray(wind, float) + np.asarray(pv, float)
    cap = _export_limit(n, cfg, export_cap)

    g = pulp.LpVariable.dicts("g", range(n), lowBound=0)             # generation used
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
        m += g[t] <= float(gen[t])                       # cannot use more than is available
        m += exp[t] == g[t] + dis[t] - ch[t]
        m += exp[t] <= float(cap[t])
        if not cfg.grid_charging_allowed:
            m += ch[t] <= g[t]                           # charge only from on-site generation
    return m, {"g": g, "ch": ch, "dis": dis, "soc": soc, "exp": exp}


def _read_plan(v: dict, wind: np.ndarray, pv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A solved window LP's plan: (battery power, curtailment fraction) per step.

    v: the model's variables, as `_energy_lp` returns them. wind, pv: the generation it was
    built on, MW per step. A variable the solver left without a value reads as 0.
    """
    gen = np.asarray(wind, float) + np.asarray(pv, float)
    n = len(gen)
    ch, dis, g = v["ch"], v["dis"], v["g"]
    p_bat = np.array([(dis[t].value() or 0.0) - (ch[t].value() or 0.0) for t in range(n)])
    used = np.array([g[t].value() or 0.0 for t in range(n)])
    curt_frac = np.where(gen > 1e-9, np.clip(1.0 - used / np.maximum(gen, 1e-9), 0, 1), 0.0)
    return p_bat, curt_frac


def default_terminal_price(eff_price: np.ndarray, run: RunConfig) -> float:
    """Value of energy left in the battery at the horizon end. OFF by default; read this first.

    The idea: stored energy will be exported later at a price the controller can partly
    choose, so value it at the upper quartile of the effective price times the discharge
    efficiency, to stop the battery emptying at every horizon end.

    The evidence says otherwise for this plant. On a receding horizon of 12 h or more,
    re-solved every step, the "horizon end" never arrives: only the first action is applied,
    and the drain it would cause is always one re-solve away. Adding this linear term instead
    made B3 hoard energy in the battery and fall below B1 on the showcase's synthetic days,
    under perfect forecasts and at 15 % forecast error alike. `hybrid.cli b3-ablation
    --synthetic-demo` measures this, and the report writes the figures to
    reports/b3_ablation_synthetic_demo.csv, so none are quoted here to go stale.

    The deeper reason is structural. A LINEAR terminal value pushes the end-of-horizon state of
    charge to a bound, because its slope is the right marginal value at one SoC level only. If
    a terminal value is needed (short horizons, long storage cycles), it should be concave and
    SoC-dependent: piecewise linear, fitted from perfect-foresight shadow prices on a
    separate training window. Project 01 met a similar failure with a value set at the retail
    import price; it now values stored energy at a monthly price fitted on a past year from
    such shadow prices, which has not been tried here.
    """
    if not run.terminal_value:
        return 0.0
    if run.terminal_price is not None:
        return run.terminal_price
    return float(np.percentile(eff_price, 75)) * run.plant.eta_d


# ------------------------------------------------------------------ B2 / B3
def b2_perfect(wind, pv, eff_price, run: RunConfig, export_cap=None,
               afrr: CapacityPrices | None = None) -> dict[str, np.ndarray]:
    """Perfect foresight: one LP over the whole window, on the true generation and prices,
    replayed through the safety layer.

    wind, pv: available generation per step, MW. eff_price: effective price per step,
    EUR/MWh (`market.effective_price`). run: the run's settings. export_cap: export limit per
    step, MW, or None for the connection.
    afrr: the window's aFRR capacity prices, or None, today's B2. With them B2 co-optimises
        aFRR reservations with its schedule (`afrr_rungs.b2_reserve`).
    Returns the dispatch arrays of `plant.simulate`. Raises RuntimeError where the LP is
    infeasible.
    """
    if afrr is not None:
        # imported here, as afrr_rungs builds on this module
        from .afrr_rungs import b2_reserve
        return b2_reserve(wind, pv, eff_price, run, afrr, export_cap)
    sol = solve_window(wind, pv, eff_price, run.dt, run, run.plant.soc_init,
                       terminal_price=0.0, export_cap=export_cap)
    if sol is None:
        raise RuntimeError("B2 infeasible")
    p_bat, curt = sol
    return simulate(wind, pv, p_bat, curt, run.dt, run.plant, export_cap=export_cap)


def b2_no_battery(wind, pv, eff_price, run: RunConfig,
                  export_cap=None) -> dict[str, np.ndarray]:
    """B2 with the battery taken out: perfect foresight that can only curtail.

    Its gain over B0 is what curtailing at low prices earns, which needs no battery; B2's gain
    over it is what the battery itself adds. Arguments and result as for `b2_perfect`.
    """
    return b2_perfect(wind, pv, eff_price, run.with_(plant=_without_battery(run.plant)),
                      export_cap)


def b3_rolling(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run: RunConfig,
               export_cap=None, resolve_every: int = 1,
               progress: bool = False, premium=None,
               afrr: CapacityPrices | None = None) -> dict[str, np.ndarray]:
    """Rolling-horizon MPC on forecast generation and prices.

    wind, pv: available generation per step in MW. eff_price: effective price per step in
    EUR/MWh, and price_fc its forecast (`make_forecasts`). wind_fc, pv_fc: generation forecasts.
    premium: None, or (rate, eligible) per step: the market premium B3 knows when it decides in
        that step (EUR/MWh, e.g. `market.planning_rates`) and whether the premium is payable
        there. eff_price and price_fc then carry each step's own rate. A plan made in step t
        prices its whole horizon at rate[t]: a later step's rate can be one B3 cannot know yet
        at t, such as the realised value of the month t lies in, which sets next month's rate.
    afrr: the window's aFRR capacity prices, or None, today's B3. With them B3 bids aFRR
        capacity at each gate and re-plans around its awards (`afrr_rungs.b3_reserve`).
    Returns the dispatch arrays of `plant.simulate`, plus per re-solve "solve_time_s" and
    "terminal_price_eur_mwh", the value the plan put on stored energy (0 without one).
    """
    if afrr is not None:
        # imported here, as afrr_rungs builds on this module
        from .afrr_rungs import b3_reserve
        return b3_reserve(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, afrr, premium,
                          export_cap, resolve_every, progress)
    cfg = run.plant
    n = len(wind)
    soc = cfg.soc_init
    p_applied = np.zeros(n)
    curt_applied = np.zeros(n)
    solve_times: list[float] = []
    terminal_prices: list[float] = []
    plan = None
    offset = 0

    for t in range(n):
        if t % resolve_every == 0 or plan is None:
            h = min(run.horizon_steps, n - t)
            w = np.concatenate([[wind[t]], wind_fc[t + 1:t + h]])
            s = np.concatenate([[pv[t]], pv_fc[t + 1:t + h]])
            pr = np.concatenate([[eff_price[t]], price_fc[t + 1:t + h]])
            if premium is not None:
                rate, eligible = premium
                pr = pr + np.where(eligible[t:t + h], rate[t] - rate[t:t + h], 0.0)
            ec = None if export_cap is None else export_cap[t:t + h]
            terminal_prices.append(default_terminal_price(pr, run))
            t0 = time.perf_counter()
            sol = solve_window(w, s, pr, run.dt, run, soc,
                               terminal_price=terminal_prices[-1], export_cap=ec)
            solve_times.append(time.perf_counter() - t0)
            plan = sol if sol is not None else (np.zeros(h), np.zeros(h))
            offset = t

        k = t - offset
        pb = float(plan[0][k]) if k < len(plan[0]) else 0.0
        cf = float(plan[1][k]) if k < len(plan[1]) else 0.0
        # Execute planned curtailment only where it is economically motivated. The plan's
        # curtailment fraction was computed on FORECAST generation and includes curtailment
        # the connection limit forced in the plan; applying that fraction to the true
        # generation discarded energy the connection could have taken. With a non-negative
        # effective price, exporting more is never worse, so the point-of-interconnection
        # enforcement in `dispatch_step` handles the cap on its own.
        if eff_price[t] >= 0.0:
            cf = 0.0

        from .plant import dispatch_step
        r = dispatch_step(float(wind[t]), float(pv[t]), soc, pb, cf, run.dt, cfg,
                          None if export_cap is None else float(export_cap[t]))
        p_applied[t], curt_applied[t] = r["p_bat"], cf
        soc = r["soc"]
        if progress and t % max(1, n // 10) == 0:
            print(f"    B3 {100 * t / n:3.0f}%", flush=True)

    res = simulate(wind, pv, p_applied, curt_applied, run.dt, cfg, export_cap=export_cap)
    res["solve_time_s"] = np.array(solve_times)
    res["terminal_price_eur_mwh"] = np.array(terminal_prices)
    return res


# ------------------------------------------------------------------ forecasts / driver
def ar_noise(n, rng, rho):
    z = rng.standard_normal(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * z[i]
    return z


def price_error_scale(eff_price: np.ndarray, run: RunConfig) -> float:
    """What B3's price-forecast error is sized on, EUR/MWh: its standard deviation is
    run.forecast_sigma times this. It is run.forecast_price_scale, or where that is None the
    standard deviation of the effective price over the first planning horizon.

    eff_price: effective price per step, EUR/MWh. run: the run's settings.
    """
    if run.forecast_price_scale is not None:
        return run.forecast_price_scale
    return float(np.std(eff_price[:run.horizon_steps]))


def make_forecasts(wind, pv, eff_price, run: RunConfig, rng):
    """B3's forecasts: the truth with AR(1) errors of relative size `run.forecast_sigma`.

    No price after a plan's horizon may reach the forecasts that plan sees. Two did. The price
    error was sized on the whole window's price spread, so a later price moved the error of
    every earlier forecast; it is sized now on `run.forecast_price_scale`, known before the
    window, or else on the effective price of the first planning horizon. And the three series
    drew from one stream in turn, so a step's PV and price errors depended on how many steps
    the window held; each series now draws from a stream of its own.

    wind, pv: available generation per step, MW, e.g. np.array([31.2, 29.8, ...]).
    eff_price: effective price per step, EUR/MWh (`market.effective_price`).
    run: the run's settings; forecast_sigma, forecast_rho, forecast_price_scale and
        horizon_steps are read.
    rng: the generator the three streams are seeded from, e.g. np.random.default_rng(0).
    Returns (wind_fc, pv_fc, price_fc), one value per step; generation is clipped at 0.
    """
    n = len(wind)
    s, r = run.forecast_sigma, run.forecast_rho
    scale = price_error_scale(eff_price, run)
    wind_rng, pv_rng, price_rng = (np.random.default_rng(seed)
                                   for seed in rng.integers(2 ** 63, size=3))
    return (np.clip(wind * (1 + s * ar_noise(n, wind_rng, r)), 0, None),
            np.clip(pv * (1 + s * ar_noise(n, pv_rng, r)), 0, None),
            eff_price + s * scale * ar_noise(n, price_rng, r))


def _refuse_premium_on_grid_energy(name: str, res: dict[str, np.ndarray],
                                   premium_rate: float | np.ndarray, market: MarketConfig,
                                   dt: float) -> None:
    """Stop the run when a rung charged grid energy while the market premium is paid.

    The premium follows renewable energy into the battery (§ 19 (3) EEG 2023); energy bought
    from the grid carries none when it is sold again. `settle` pays the premium on all
    eligible export, and the ladder does not track which part of the stored energy came from
    the grid, so a rung that buys, stores and resells would be paid the premium on energy it
    never generated. No shipped rung buys grid energy; this guards a controller that would.

    name: rung name, for the message. res: the rung's dispatch, with "import" and "p_ch"
    per step in MW (the plant imports only to charge, so import <= p_ch).
    premium_rate: market premium in EUR/MWh, one rate or one per step.
    market: the run's market settings. dt: step length in hours.
    Raises NotImplementedError when more than _GRID_SHARE_TOL of the charged energy came
    from the grid while a premium is paid.
    """
    bought = float(np.sum(res["import"]) * dt)
    charged = float(np.sum(res["p_ch"]) * dt)
    premium_paid = market.eeg_enabled and bool(np.any(np.asarray(premium_rate) > 0.0))
    if premium_paid and bought > _GRID_SHARE_TOL * charged:
        raise NotImplementedError(
            f"{name} charged {charged:,.2f} MWh, {bought:,.2f} MWh of it from the grid, while "
            "the market premium is paid; the premium is due on renewable energy only, and the "
            "ladder cannot tell grid energy from renewable energy in the battery")


def run_ladder(wind, pv, price, run: RunConfig, premium_rate: float,
               rebap=None, export_cap=None, include_b3=True,
               progress=False, schedule=None, b3_premium_rate=None,
               include_b2_no_battery=False,
               afrr: CapacityPrices | None = None) -> dict[str, dict]:
    """Run every rung on the same window, settle it, and check the ladder invariant.

    wind, pv: available generation per step in MW, e.g. np.array([31.2, 29.8, ...]).
    price: day-ahead price per step in EUR/MWh.
    premium_rate: the market premium every rung is settled on, EUR/MWh, one rate or one per
        step: the realised value (`market.premium_rates`). B2 plans on it, as perfect
        foresight may; B0 and B1 read no prices.
    b3_premium_rate: the premium B3 plans on, one rate or one per step: in each step the rate
        B3 knows when it decides there, e.g. `market.planning_rates`, the previous month's
        realised rate. B3 prices each plan's whole horizon at the rate of the step it decides
        in. None plans B3 on premium_rate, which B3 is then taken to know: right for an
        assumed constant rate, a look-ahead for a realised one. A premium_rate that changes
        within the window is a realised value, so it is refused without b3_premium_rate.
    rebap, schedule: imbalance price in EUR/MWh and the export the plant committed to, in MW,
        both per step. Each rung's deviation from the schedule is settled at reBAP only when
        both are given; no rung commits a schedule of its own, so reBAP alone settles nothing.
        The settlement's imbalance_cost is the deviation priced at reBAP minus spot.
    export_cap: per-step export limit in MW (a redispatch order), or None for the connection.
    include_b2_no_battery: also run "B2 no battery" (`b2_no_battery`), planned and settled as
        B2 is; the invariant then holds it below B2 as well.
    afrr: the window's aFRR capacity prices (`afrr_data.capacity_prices`), with
        run.market.afrr_enabled; or None, with it off, for a ladder without aFRR. B2 then
        reserves aFRR capacity paid on run.market.afrr_price_basis (`afrr_rungs.b2_reserve`),
        B3 bids it at each gate and is paid its accepted bids (`afrr_rungs.b3_reserve`), and
        every rung is settled with the six aFRR terms of `market.settle`: B3 on its bids, the
        others on B2's payment; B0, B1 and "B2 no battery" hold nothing, so they get 0 on
        them. A rung may then out-earn B2 by PLAN_MARGIN times its own capacity revenue
        beyond the tolerance: B2 plans its reservations with that margin, so it is a ceiling
        only up to it.
    Returns {rung name: dispatch arrays plus "cost" (the settlement) and "violations"}.
    Raises AssertionError when a rung beats perfect foresight B2 on revenue before imbalance,
    NotImplementedError when a rung charged grid energy while the market premium is paid, and
    ValueError when B3 runs on a premium_rate that changes within the window and no
    b3_premium_rate is given. With aFRR, it also raises what
    `afrr_rungs.refuse_what_afrr_cannot_run` raises, e.g. for B3 on the "average" basis.
    """
    from .plant import check_feasible

    # Planned on such a rate, B3 would know realised values before they are published; and as
    # B3 prices a horizon at its decision step's rate, it would not even plan on them as given.
    if include_b3 and b3_premium_rate is None and np.ptp(np.atleast_1d(premium_rate)) > 0:
        raise ValueError("premium_rate changes within the window, so it is a realised value "
                         "B3 cannot know when it decides; pass b3_premium_rate, the rate B3 "
                         "knows in each step (e.g. market.planning_rates)")
    # Every aFRR step is guarded, so that without aFRR the ladder calls its rungs and settles
    # them with the arguments it always did.
    if afrr is not None or run.market.afrr_enabled:
        from .afrr_rungs import refuse_what_afrr_cannot_run  # it builds on this module
        refuse_what_afrr_cannot_run(afrr, run, len(price), export_cap, include_b3)
    with_afrr = {} if afrr is None else {"afrr": afrr}
    paid = ({} if afrr is None else
            {"afrr": Payment(*paid_prices(afrr, run.market.afrr_price_basis), afrr.block)})
    # B3 bids, so it is paid its accepted bids, whatever B2 is paid
    paid_b3 = {} if afrr is None else {"afrr": Payment(*paid_prices(afrr, "bid"), afrr.block)}

    rng = np.random.default_rng(run.seed)
    # The step length counts the hours of a negative-price run, so planning must use the one
    # the settlement uses; without it, B2 and B3 planned every step as a quarter-hour.
    eff = effective_price(price, run.market, premium_rate, run.dt)

    out = {
        "B0 no battery": b0_no_battery(wind, pv, run, export_cap),
        "B1 curtailment avoidance": b1_curtailment_avoidance(wind, pv, run, export_cap),
        "B2 perfect foresight": b2_perfect(wind, pv, eff, run, export_cap, **with_afrr),
    }
    if include_b2_no_battery:
        out["B2 no battery"] = b2_no_battery(wind, pv, eff, run, export_cap)
    if include_b3:
        # B3 plans on the premium it can know, not on the realised value it is settled on: a
        # month's market value is known only once the month is over.
        plan = premium_rate if b3_premium_rate is None else b3_premium_rate
        # A known limit, optimistic for B3: its effective price and premium mask come from the
        # true prices, so B3 knows exactly in which steps the premium is lost, also where the
        # day-ahead auction has not cleared yet when it decides; and under a whole-run rule
        # the mask at a step depends on how long the run lasts, possibly past the horizon. A
        # forecaster would see those steps only through its own price forecast.
        eff_b3 = effective_price(price, run.market, plan, run.dt)
        wind_fc, pv_fc, price_fc = make_forecasts(wind, pv, eff_b3, run, rng)
        eligible = (premium_eligible(price, run.market, run.dt) if run.market.eeg_enabled
                    else np.zeros(len(price), dtype=bool))
        rate = np.broadcast_to(np.asarray(plan, dtype=float), (len(price),))
        out["B3 rolling MPC"] = b3_rolling(wind, pv, eff_b3, wind_fc, pv_fc, price_fc, run,
                                           export_cap, progress=progress,
                                           premium=(rate, eligible), **with_afrr)
        out["B3 rolling MPC"]["price_error_scale_eur_mwh"] = price_error_scale(eff_b3, run)

    # Single-price settlement pays spot on the committed schedule and reBAP on the deviation.
    # settle() pays spot on the whole export, so it must price the deviation at the spread,
    # reBAP minus spot; handed reBAP itself, it paid the deviation twice.
    spread = None if rebap is None else (np.asarray(rebap, dtype=float)
                                         - np.asarray(price, dtype=float))
    for name, r in out.items():
        _refuse_premium_on_grid_energy(name, r, premium_rate, run.market, run.dt)
        r["cost"] = settle(r, price, run.dt, run.plant, run.market,
                           rebap=spread, schedule=schedule, premium_rate=premium_rate,
                           **(paid_b3 if name == "B3 rolling MPC" else paid))
        r["violations"] = check_feasible(r, rung_plant(name, run.plant))

    # Perfect foresight cannot lose to ANY other rung. Checking only B3 would have missed the
    # premium-feedback bug, which showed up as B2 scoring below the do-nothing baseline.
    # B2 is the ceiling of what it optimises, and imbalance lies outside that: the committed
    # schedule and reBAP are not in its objective, so a rung that deviates the lucky way can
    # out-earn it there. The check therefore compares revenue before imbalance.
    before = {name: r["cost"]["net_revenue"] + r["cost"]["imbalance_cost"]
              for name, r in out.items()}
    b2 = before["B2 perfect foresight"]
    tol = ladder_tolerance(b2)
    for name, rev in before.items():
        # B2 holds its reservations with PLAN_MARGIN to spare, so a rung that holds r MW
        # without it earns at most that share of its capacity revenue more than B2 could by
        # holding r / (1 + PLAN_MARGIN)
        allowed = (tol if afrr is None
                   else tol + PLAN_MARGIN * out[name]["cost"]["afrr_capacity_revenue"])
        if name != "B2 perfect foresight" and rev > b2 + allowed:
            raise AssertionError(
                f"ladder invariant violated: {name} ({rev:,.0f}) beats perfect foresight "
                f"B2 ({b2:,.0f}) before imbalance - impossible, so something is wrong")
    return out

"""The aFRR capacity model's own parts: its types, its payments, and the terms its
reservations add to a window LP. Nothing here reads a file or calls a solver.

The optimisers, the safety layer and the settlement read what is defined here, so that none
of them imports `afrr_data`, which reads regelleistung.net's files.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import NamedTuple

import numpy as np
import pulp

# In config, beside the settings they validate (MarketConfig.afrr_*), so that this module
# may read config and config needs nothing from here.
from .config import AFRR_MIN_ENERGY_HOURS as MIN_ENERGY_HOURS
from .config import AFRR_PRICE_BASES as PRICE_BASES
from .config import PlantConfig, RunConfig

# B2 plans the energy and the export of what it reserves (R4-R6 in `add_reserve_terms`) on
# r x (1 + PLAN_MARGIN), and is paid r. Its single plan is replayed open-loop through the
# safety layer, and PuLP reads CBC's solution to about eight significant digits, so the
# replayed state of charge drifts from the planned one; where the plan empties the battery
# to hold its export at r_down, the layer's clip multiplies that drift. In full-year
# prototype replays outside the repository, this margin kept the residue far below
# plant.TOL at a negligible cost. B3 re-plans from its true state every step and plans
# whole MW, where the margin would cost a whole MW, so it plans without it.
PLAN_MARGIN = 1e-4


@dataclass(frozen=True)
class ReserveRule:
    """How much energy an aFRR reservation keeps in the battery, per reserved direction.

    Positive reserve of r MW keeps max(r x hours_up, floor_mwh) MWh it can deliver; negative
    reserve keeps max(r x hours_down, floor_mwh) MWh of room it can absorb (PQ conditions
    §§ 1.6, 2.7.1-2.7.3; MarketConfig's afrr_* fields give the defaults and their reasons).
    hours_up, hours_down: hours, finite and at least MIN_ENERGY_HOURS (1 hour, the 60 minutes
        of PQ § 2.7.1), e.g. 4.0 and 1.0.
    floor_mwh: MWh, finite and at least 0, e.g. 5.0, 15 minutes of a 20 MW battery; 0 drops
        the floor, as B2 plans.
    Raises ValueError for any other value.
    """
    hours_up: float
    hours_down: float
    floor_mwh: float

    def __post_init__(self) -> None:
        # the safety layer reads its rule from here, and its slack on the bands (plant.TOL_SOC)
        # would count as reserve not held on a band shorter than MIN_ENERGY_HOURS
        for name, least in (("hours_up", MIN_ENERGY_HOURS), ("hours_down", MIN_ENERGY_HOURS),
                            ("floor_mwh", 0.0)):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= least):
                raise ValueError(f"ReserveRule.{name} must be finite and at least {least:g}, "
                                 f"not {value!r}")


@dataclass(frozen=True)
class CapacityPrices:
    """The window's aFRR capacity products, B3's gates and bids, and the realised prices.

    Every field holds one value per 15-minute step of the window, as
    `afrr_data.capacity_prices` maps them. Prices are in EUR/MW/h, "up" for positive reserve
    (the POS products) and "down" for negative reserve (the NEG products).
    block: the step's product, a running id per Berlin date and 4-hour slice, increasing in
        time and shared by both directions, e.g. [0, 0, ..., 1, ...].
    day: the step's Berlin day, 0 for the window's first.
    gate, award: True at 09:00 and at 09:30 Berlin on a day whose next day lies in the
        window. B3 bids for all of the next day at the gate and learns at the award which
        bids won (MfRRA § 20 (3)).
    average_up, average_down: the realised German volume-weighted average of the product.
    marginal_up, marginal_down: the realised German marginal, the highest accepted bid.
    bid_up, bid_down: B3's bid, the same product's German average on the previous Berlin
        day, to whole cents; NaN on the window's first day, which has no gate in the window.
    Every field is kept as a read-only copy of what is given, so no rung can change the prices
    another rung reads. Raises ValueError unless every field holds one value per step.
    """
    block: np.ndarray
    day: np.ndarray
    gate: np.ndarray
    award: np.ndarray
    average_up: np.ndarray
    average_down: np.ndarray
    marginal_up: np.ndarray
    marginal_down: np.ndarray
    bid_up: np.ndarray
    bid_down: np.ndarray

    def __post_init__(self) -> None:
        # Frozen all the way down. pandas 3 hands out read-only arrays and pandas 2 writable
        # views of its frames, so without the copy an in-place write would raise on one and
        # silently change the realised prices of every later rung on the other.
        shapes = {}
        for field in fields(self):
            array = np.array(getattr(self, field.name))
            array.setflags(write=False)
            object.__setattr__(self, field.name, array)
            shapes[field.name] = array.shape
        if len(set(shapes.values())) != 1 or len(shapes["block"]) != 1:
            raise ValueError(f"CapacityPrices holds one value per step in every field, not "
                             f"the shapes {shapes}")


def paid_prices(prices: CapacityPrices, basis: str) -> tuple[np.ndarray, np.ndarray]:
    """What a reservation is paid per step, EUR/MW/h, up and down.

    The auction pays as bid (BK6-15-158, Tenor 9a) and accepts bids from the lowest price up
    (MfRRA § 20 (8)), so a bid below the realised marginal is accepted. A bid equal to it is
    counted as accepted too, which is an assumption: § 20 (8)(c) settles equal prices by
    lot, and a bid at the marginal may be accepted in part. The battery's few MW are taken
    not to move the marginal.
    For the settlement only. "bid" reads the realised marginal, which is the 09:30 award, so
    B3 must not plan on it at its 09:00 gate: there it knows its bids (bid_up, bid_down) and
    nothing of the award, prices day D's products at those bids, and counts only a missing
    or zero bid as unpaid.
    prices: the window's CapacityPrices.
    basis: "bid", the bid where it was accepted, and 0 where it was not or where there is no
        bid (NaN, on the window's first day); or, as B2's sensitivities, "average", what the
        average accepted MW earned, or "marginal", the most any bid could have earned.
    Returns (up, down), one price per step: new arrays on "bid", and the realised arrays of
        `prices`, which are read-only, on "average" and "marginal".
    Raises ValueError for any other basis.
    """
    if basis == "bid":
        # NaN <= x is False, so a step without a bid is paid nothing
        return (np.where(prices.bid_up <= prices.marginal_up, prices.bid_up, 0.0),
                np.where(prices.bid_down <= prices.marginal_down, prices.bid_down, 0.0))
    if basis == "average":
        return prices.average_up, prices.average_down
    if basis == "marginal":
        return prices.marginal_up, prices.marginal_down
    raise ValueError(f"unknown aFRR price basis {basis!r}; it is one of {PRICE_BASES}")


class Payment(NamedTuple):
    """What one rung is paid for aFRR capacity, per 15-minute step, and each step's product.

    up, down: EUR/MW/h per step, e.g. `paid_prices(prices, "bid")`; 0 where nothing is paid.
    block: the step's product, e.g. CapacityPrices.block, so that reserve not held is
        charged per product (`product_shortfall`).
    """
    up: np.ndarray
    down: np.ndarray
    block: np.ndarray


def product_shortfall(deficit: np.ndarray, block: np.ndarray,
                      dt: float) -> tuple[np.ndarray, float]:
    """Reserve not held, per product: each step gets its product's largest shortfall.

    A product is sold for all its hours, so a shortfall in any of its steps counts for the
    whole product. MfRRA prices the MWh not available per quarter-hour (§ 25 (2)(a)(i)(2));
    charging the product's largest shortfall for all of it charges at least as much.
    deficit: MW not held per step, e.g. `plant.simulate`'s "afrr_deficit_up".
    block: the step's product, any labels, e.g. [0, 0, ..., 1, ...].
    dt: step length, h, e.g. 0.25.
    Returns (per step, the MW its product did not hold; the MWh not held, which is that
    array's sum times dt). Raises ValueError unless deficit and block have one shape.
    """
    deficit, block = np.asarray(deficit, dtype=float), np.asarray(block)
    if deficit.shape != block.shape:
        raise ValueError(f"deficit {deficit.shape} and block {block.shape} must hold one "
                         "value per step each")
    labels, product = np.unique(block, return_inverse=True)
    peak = np.zeros(len(labels))
    np.maximum.at(peak, product, deficit)
    per_step = peak[product]
    return per_step, float(np.sum(per_step) * dt)


@dataclass(frozen=True)
class ReserveSpec:
    """The aFRR reservations of one plan, product by product, as `add_reserve_terms` reads them.

    The plan's products are numbered from 0 in time order. Per product and direction ("up"
    for positive reserve, "down" for negative):
    fixed_up, fixed_down: NaN where the plan decides the reservation; otherwise the MW it
        must hold, finite and at least 0. It may fall short of them only at the penalty plus
        their payment per MWh not held, as the settlement charges it.
    paid_up, paid_down: what a reservation is paid, EUR/MW/h, finite and at least 0, and
        above 0 where decided: a product that pays nothing is fixed at 0.
    hours: the hours the product is paid and charged for, above 0, e.g. 4.0, or 3.0 and 5.0
        on the daylight-saving days.
    For the plan as a whole:
    block: per step, the step's product, an integer from 0 in time order, each at least once.
    rule: the energy each reservation keeps (ReserveRule).
    penalty: EUR per MWh of reserve not held, finite and above 0.
    integer: True decides in whole MW, each decision with a binary indicator that carries
        the floor (rule.floor_mwh); False decides continuously, which needs a floor of 0.
    margin: decisions plan their energy and export on r x (1 + margin) and are paid r,
        e.g. PLAN_MARGIN; fixed reservations plan on r. Finite and at least 0.
    The arrays are kept as read-only copies. Raises ValueError where the fields do not fit.
    """
    fixed_up: np.ndarray
    fixed_down: np.ndarray
    paid_up: np.ndarray
    paid_down: np.ndarray
    hours: np.ndarray
    block: np.ndarray
    rule: ReserveRule
    penalty: float
    integer: bool
    margin: float

    def __post_init__(self) -> None:
        for name in ("fixed_up", "fixed_down", "paid_up", "paid_down", "hours", "block"):
            array = np.array(getattr(self, name), dtype=None if name == "block" else float)
            array.setflags(write=False)
            object.__setattr__(self, name, array)
        problem = _spec_problem(self)
        if problem:
            raise ValueError(f"ReserveSpec: {problem}")

    @classmethod
    def decide_all(cls, block: np.ndarray, paid_up: np.ndarray, paid_down: np.ndarray,
                   run: RunConfig, integer: bool, floor_mwh: float,
                   margin: float) -> ReserveSpec:
        """Every product of a window left to the plan, but those that pay nothing, fixed at 0.

        block: per step, the window's product labels, increasing in time, e.g.
            CapacityPrices.block. paid_up, paid_down: EUR/MW/h per step, one price per
            product, e.g. `paid_prices(prices, "bid")`. run: dt, and the energy hours and
            the penalty of run.market. integer, margin: as ReserveSpec takes them.
        floor_mwh: the floor, MWh, e.g. 0.0, as B2 relaxes it.
        Raises ValueError where a paid price changes within a product.
        """
        product = np.unique(np.asarray(block), return_inverse=True)[1].ravel()
        first = np.unique(product, return_index=True)[1]
        paid = [np.asarray(p, dtype=float) for p in (paid_up, paid_down)]
        if any(not np.array_equal(p[first][product], p) for p in paid):
            raise ValueError("a reservation is paid one price per product and direction")
        up, down = (p[first] for p in paid)
        market = run.market
        return cls(fixed_up=np.where(up > 0, np.nan, 0.0),
                   fixed_down=np.where(down > 0, np.nan, 0.0), paid_up=up, paid_down=down,
                   hours=np.bincount(product) * run.dt, block=product,
                   rule=ReserveRule(market.afrr_energy_hours_up,
                                    market.afrr_energy_hours_down, floor_mwh),
                   penalty=market.afrr_shortfall_penalty_eur_mwh, integer=integer,
                   margin=margin)


def _spec_problem(spec: ReserveSpec) -> str | None:
    """What does not fit in `spec`, in words, or None where everything does."""
    fixed, paid = (spec.fixed_up, spec.fixed_down), (spec.paid_up, spec.paid_down)
    count = len(spec.hours)
    if count == 0 or any(a.shape != (count,) for a in (*fixed, *paid, spec.hours)):
        return "fixed_*, paid_* and hours hold one value per product, for at least one"
    block = spec.block
    if (block.ndim != 1 or not np.issubdtype(block.dtype, np.integer)
            or np.any(np.diff(block) < 0)
            or not np.array_equal(np.unique(block), np.arange(count))):
        return "block numbers each step's product from 0 in time order, each at least once"
    if any(np.any(~np.isnan(f) & ~(np.isfinite(f) & (f >= 0))) for f in fixed):
        return "a fixed reservation is finite and at least 0 MW, or NaN where decided"
    if any(not np.all(np.isfinite(p) & (p >= 0)) for p in paid):
        return "paid_* are finite and at least 0 EUR/MW/h"
    if any(np.any(np.isnan(f) & (p <= 0)) for f, p in zip(fixed, paid)):
        return "a product that pays nothing is fixed at 0, not decided"
    if not np.all(np.isfinite(spec.hours) & (spec.hours > 0)):
        return "hours are finite and above 0"
    if not (math.isfinite(spec.penalty) and spec.penalty > 0):
        return "penalty is finite and above 0 EUR/MWh"
    if not (math.isfinite(spec.margin) and spec.margin >= 0):
        return "margin is finite and at least 0"
    if not spec.integer and spec.rule.floor_mwh > 0 and any(np.isnan(f).any() for f in fixed):
        # the floor asks a reservation above 0 for floor_mwh, whatever its size, which only a
        # binary indicator can express
        return ("a continuous decision cannot keep the floor; decide in whole MW, or set "
                "the floor to 0, as B2 does")
    return None


class ReservePlan(NamedTuple):
    """A solved plan with aFRR reservations (`afrr_rungs.solve_window_reserve`), per step:
    p_bat: battery power, MW, > 0 discharging. curt: share of generation curtailed, 0 to 1.
    r_up, r_down: MW reserved, from 0 to the battery's rating; whole MW where decided so.
    soc: the energy stored at the end of the step as planned, MWh.
    And objective: the plan's value, EUR: the window LP's energy terms, plus the capacity
    paid, less the cut and the penalty of reserve the plan does not hold.
    """
    p_bat: np.ndarray
    curt: np.ndarray
    r_up: np.ndarray
    r_down: np.ndarray
    soc: np.ndarray
    objective: float


def whole_megawatts(plan: ReservePlan, spec: ReserveSpec, soc0: float, floor_mwh: float,
                    plant: PlantConfig) -> tuple[np.ndarray, np.ndarray]:
    """A continuous plan's reservations as whole-MW bids under the floor could hold them.

    Per product and direction the reservation is rounded down to whole MW, and set to 0
    where that leaves less than 1 MW, or where the plan's state of charge misses the floor
    at a step boundary of the product. Every limit of the plan is monotone in its
    reservations and the floor is checked, so the plan's energy schedule holds the rounded
    reservations too: they keep the rules of a whole-MW plan (B3's).
    plan: the solved plan. spec: its products. soc0: energy stored at its start, MWh.
    floor_mwh: the floor the rounded reservations keep, MWh, e.g. 5.0.
    plant: the plant, for its state-of-charge limits and efficiencies.
    Returns (up, down), MW per step of the plan.
    """
    edges = np.r_[soc0, plan.soc]
    lowest, highest = np.full(len(spec.hours), np.inf), np.full(len(spec.hours), -np.inf)
    # the state of charge is linear within a step, so its two ends bound it
    np.minimum.at(lowest, spec.block, np.minimum(edges[:-1], edges[1:]))
    np.maximum.at(highest, spec.block, np.maximum(edges[:-1], edges[1:]))
    keeps_up = lowest >= plant.soc_min + floor_mwh / plant.eta_d
    keeps_down = highest <= plant.soc_max - floor_mwh * plant.eta_c
    # rounding down leaves 0 where less than 1 MW was reserved
    return (np.where(keeps_up[spec.block], np.floor(plan.r_up), 0.0),
            np.where(keeps_down[spec.block], np.floor(plan.r_down), 0.0))


def rounding_bound(plan: ReservePlan, spec: ReserveSpec, soc0: float, floor_mwh: float,
                   plant: PlantConfig) -> float:
    """The capacity revenue a continuous plan without the floor loses when its reservations
    are rounded as `whole_megawatts` rounds them, EUR.

    The rounded plan keeps the whole-MW rules with the floor and the same energy schedule,
    so the best whole-MW plan with the floor earns at least the continuous plan's objective
    less this: an upper bound on what continuous MW and a relaxed floor overstate.
    It also counts what spec.margin costs in whole MW, so it is loose where the margin holds
    a reservation just below a whole MW: at a connection that binds, negative reserve's
    export holds it to the connection / (1 + margin), and rounding drops a whole MW that a
    whole-MW plan without the margin, as B3 plans, could hold.
    Arguments as for `whole_megawatts`; spec's paid prices and hours price the loss as the
    plan's objective counts its capacity.
    """
    up, down = whole_megawatts(plan, spec, soc0, floor_mwh, plant)
    first = np.unique(spec.block, return_index=True)[1]
    lost = (spec.paid_up * (plan.r_up - up)[first]
            + spec.paid_down * (plan.r_down - down)[first]) * spec.hours
    return float(np.sum(lost))


class _Held(NamedTuple):
    """One product's reservation in one direction, as LP terms.

    r: the MW reserved, a decision's variable or a fixed number. u: the MW of a fixed one the
    plan may fail to hold, a variable from 0 to r; 0 for a decision, which holds all of r.
    z: a whole-MW decision's indicator, 1 where it reserves, which carries the floor, or None.
    k: what the plan holds r at: 1 + the margin for a decision, 1 for a fixed one.
    """
    r: pulp.LpVariable | float
    u: pulp.LpVariable | float
    z: pulp.LpVariable | None
    k: float


def add_reserve_terms(m: pulp.LpProblem, v: dict, spec: ReserveSpec, plant: PlantConfig,
                      soc0: float, cap: np.ndarray) -> tuple[list, list]:
    """Add `spec`'s aFRR reservations to a window LP of `baselines._energy_lp`.

    On every step of a product that a direction reserves or decides: battery-power headroom
    (R1 up, R2 down), connection headroom for positive reserve (R3), and export of at least
    the negative reservation, so that a call never draws from the grid (R4). At both ends of
    the step, the energy band of each such direction (R5 up, R6 down) and its floor (R7):
    the state of charge is linear within a step, so this holds the energy at every instant,
    as the safety layer measures it. The objective gains the settlement's terms
    (`market.settle`): the capacity paid, c x r x hours, and for a fixed reservation the
    penalty plus c per MWh not held, on u for all of its product's hours. A product that a
    direction neither reserves nor decides adds nothing, so a spec with every reservation
    fixed at 0 leaves `m` as it was.
    u is one variable per product, as the settlement charges a product's largest shortfall
    for all of it: once a fixed reservation falls short, the plan may move the state of
    charge within that shortfall for the rest of the product at no further cost. The safety
    layer lets the battery move no further past a band than it already is
    (`plant.battery_interval`), so a replay may not follow such a move and then earns less
    than the plan's objective. Decisions have no slack, so for them the two agree.
    m, v: the model and its variables (`baselines._energy_lp`). spec: the reservations, one
        product per step of the model. plant: the plant the model was built for.
    soc0: energy stored at the start, MWh. cap: export limit per step, MW.
    Returns (up, down): per product, the reservation as the model holds it, a variable where
    decided and a number where fixed, to read the plan with once it is solved.
    Raises ValueError unless spec holds one product per step of the model.
    """
    if len(spec.block) != len(v["soc"]):
        raise ValueError(f"spec has {len(spec.block)} steps and the model {len(v['soc'])}")
    up, down = ([_held(m, spec, direction, b, plant) for b in range(len(spec.hours))]
                for direction in ("up", "down"))
    if all(held is None for held in up + down):
        return [0.0] * len(up), [0.0] * len(down)
    for t, b in enumerate(spec.block):
        _add_headroom(m, v, t, up[b], down[b], plant.bess_mw, float(cap[t]))
        if t == 0 or spec.block[t - 1] != b:
            start = soc0 if t == 0 else v["soc"][t - 1]
            _add_bands(m, start, f"{b}_start", up[b], down[b], spec.rule, plant)
        _add_bands(m, v["soc"][t], f"{b}_{t}", up[b], down[b], spec.rule, plant)
    m.setObjective(m.objective + _reserve_value(spec, up, down))
    return ([0.0 if held is None else held.r for held in up],
            [0.0 if held is None else held.r for held in down])


def _held(m: pulp.LpProblem, spec: ReserveSpec, direction: str, b: int,
          plant: PlantConfig) -> _Held | None:
    """Product b's reservation in `direction` ("up" or "down") as LP terms, or None where it
    is fixed at 0. A whole-MW decision under a floor gets its indicator, tied to it here."""
    fixed = float(getattr(spec, f"fixed_{direction}")[b])
    if fixed == 0.0:
        return None
    if not math.isnan(fixed):
        return _Held(fixed, pulp.LpVariable(f"u_{direction}_{b}", 0.0, fixed), None, 1.0)
    category = pulp.LpInteger if spec.integer else pulp.LpContinuous
    r = pulp.LpVariable(f"r_{direction}_{b}", 0.0, plant.bess_mw, cat=category)
    z = None
    if spec.integer and spec.rule.floor_mwh > 0:
        # 0 means no bid, so whole MW give the 1 MW minimum on their own; the floor needs z
        z = pulp.LpVariable(f"z_{direction}_{b}", cat=pulp.LpBinary)
        m += r <= plant.bess_mw * z, f"z_{direction}_{b}_reserves"
        m += r >= z, f"z_{direction}_{b}_holds"
    return _Held(r, 0.0, z, 1.0 + spec.margin)


def _add_headroom(m: pulp.LpProblem, v: dict, t: int, up: _Held | None, down: _Held | None,
                  rating: float, cap: float) -> None:
    """R1-R4 on step t for each direction its product holds (None: holds nothing)."""
    if up is not None:
        m += v["dis"][t] - v["ch"][t] + up.r <= rating, f"R1_{t}"
        m += v["exp"][t] + up.r <= cap, f"R3_{t}"
    if down is not None:
        m += v["ch"][t] - v["dis"][t] + down.r <= rating, f"R2_{t}"
        m += v["exp"][t] >= down.k * down.r - down.u, f"R4_{t}"


def _add_bands(m: pulp.LpProblem, soc, name: str, up: _Held | None, down: _Held | None,
               rule: ReserveRule, plant: PlantConfig) -> None:
    """R5-R7 at one instant, the end of a step or the start of a product: `soc`, the energy
    stored then (a variable, or the window's first MWh), keeps the band and the floor of
    each direction the product holds."""
    if up is not None:
        held = up.k * up.r - up.u
        m += soc >= plant.soc_min + held * (rule.hours_up / plant.eta_d), f"R5_{name}"
        floor = _floor(up, rule.floor_mwh, rule.hours_up)
        if floor is not None:
            m += soc >= plant.soc_min + floor * (1.0 / plant.eta_d), f"R7_up_{name}"
    if down is not None:
        held = down.k * down.r - down.u
        m += soc <= plant.soc_max - held * (rule.hours_down * plant.eta_c), f"R6_{name}"
        floor = _floor(down, rule.floor_mwh, rule.hours_down)
        if floor is not None:
            m += soc <= plant.soc_max - floor * plant.eta_c, f"R7_down_{name}"


def _floor(held: _Held, floor_mwh: float, hours: float):
    """The MWh the floor keeps for `held` (R7), or None where it asks no more than the band:
    a whole-MW decision keeps floor_mwh where its indicator is 1, and a fixed r MW keeps
    floor_mwh x (r - u) / r, the share the plan holds, where floor_mwh exceeds r x hours."""
    if floor_mwh <= 0:
        return None
    if held.z is not None:
        return floor_mwh * held.z
    if isinstance(held.r, float) and floor_mwh > held.r * hours:
        return (held.r - held.u) * (floor_mwh / held.r)
    return None


def _reserve_value(spec: ReserveSpec, up: list, down: list) -> pulp.LpAffineExpression:
    """The objective's aFRR terms, as the settlement counts them: per product and direction,
    c x r x hours paid, less (penalty + c) x u x hours, the cut and the penalty of the MW of a
    fixed reservation not held."""
    terms = []
    for b, hours in enumerate(spec.hours):
        for held, paid in ((up[b], spec.paid_up[b]), (down[b], spec.paid_down[b])):
            if held is not None:
                terms.append(float(paid * hours) * held.r
                             - float((spec.penalty + paid) * hours) * held.u)
    return pulp.lpSum(terms)

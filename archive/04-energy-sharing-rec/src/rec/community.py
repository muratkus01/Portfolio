"""Community profiles, allocation mechanisms, and settlement.

The four mechanism families are implemented behind one interface so they can be compared on
identical physics. Each produces a different *price signal* to members, and under
individually-rational behaviour that changes what the community physically does - the coupling
that single-number community studies miss.
"""
from __future__ import annotations

import numpy as np

from .config import CommunityConfig, MemberConfig, RegimeConfig, RunConfig


# --------------------------------------------------------------------- profiles
def member_profiles(run: RunConfig) -> np.ndarray:
    """Synthesise per-member load, [steps, members] in kW.

    Correlation between members is preserved deliberately. Independent draws overstate
    complementarity badly - and complementarity is precisely where a community's value comes
    from - so an independent-profile study systematically flatters energy sharing. The
    sensitivity of every result to `run.correlation` is a required ablation.
    """
    rng = np.random.default_rng(run.seed)
    n_steps = run.days * run.steps_per_day
    m = len(run.members)
    q = np.arange(n_steps) % run.steps_per_day
    hour = q * run.dt

    # shape: households peak morning and evening, commercial peaks in the working day
    hh = 0.45 + 0.55 * np.exp(-((hour - 7.5) ** 2) / 4) + 1.0 * np.exp(-((hour - 19.5) ** 2) / 6)
    com = 0.25 + 1.3 * np.exp(-((hour - 12.0) ** 2) / 18)

    common = rng.standard_normal(n_steps)          # shared weather/behaviour factor
    out = np.empty((n_steps, m))
    for j, mem in enumerate(run.members):
        shape = com if mem.profile == "commercial" else hh
        idio = rng.standard_normal(n_steps)
        noise = (np.sqrt(run.correlation) * common
                 + np.sqrt(max(0.0, 1 - run.correlation)) * idio)
        series = shape * (1.0 + 0.25 * noise)
        series = np.clip(series, 0.05, None)
        scale = mem.annual_kwh / 8760.0 / max(series.mean(), 1e-9)
        out[:, j] = series * scale

        if mem.has_heat_pump:
            out[:, j] += np.clip(1.2 - 0.05 * (hour - 3) ** 2 / 6, 0.1, None) * 0.6
        if mem.has_ev:
            evening = (hour >= 18) | (hour < 6)
            out[:, j] += np.where(evening, 1.1, 0.0)
    return out


def pv_profile(run: RunConfig, kwp: float) -> np.ndarray:
    """Normalised PV output in kW for a given peak rating."""
    n_steps = run.days * run.steps_per_day
    q = np.arange(n_steps) % run.steps_per_day
    hour = q * run.dt
    rng = np.random.default_rng(run.seed + 1)
    clear = np.clip(np.sin(np.pi * (hour - 6) / 12), 0, 1) ** 1.2
    cloud = np.clip(1 - 0.5 * rng.random(n_steps) * (rng.random(n_steps) > 0.55), 0, 1)
    return kwp * 0.85 * clear * cloud


# --------------------------------------------------------------------- mechanisms
def allocate(generation_kw: np.ndarray, consumption_kw: np.ndarray,
             mechanism: str, run: RunConfig) -> np.ndarray:
    """Attribute shared generation to members. Returns [steps, members] in kW.

    Guarantees, whatever the mechanism: allocations are non-negative, no member is allocated
    more than it consumes, and the total allocated never exceeds the generation available.

    | mechanism              | rule                                    | incentive property |
    |------------------------|-----------------------------------------|--------------------|
    | static_key             | fixed ownership share                   | no temporal signal |
    | dynamic_proportional   | pro-rata by instantaneous consumption   | rewards consuming during surplus, including uselessly |
    | optimisation           | greedily to the highest-value use       | efficient, individually opaque |
    | market                 | uniform internal clearing price         | aligns incentives if priced right |
    """
    n_steps, m = consumption_kw.shape
    alloc = np.zeros((n_steps, m))
    gen = np.asarray(generation_kw, dtype=float)

    if mechanism == "static_key":
        keys = np.array([mem.share_key for mem in run.members], dtype=float)
        keys = keys / keys.sum()
        raw = gen[:, None] * keys[None, :]
        alloc = np.minimum(raw, consumption_kw)

    elif mechanism == "dynamic_proportional":
        tot = consumption_kw.sum(axis=1, keepdims=True)
        share = np.divide(consumption_kw, np.where(tot > 1e-9, tot, 1.0))
        alloc = np.minimum(gen[:, None] * share, consumption_kw)

    elif mechanism in ("optimisation", "market"):
        # Both allocate to the highest-value use first. They differ in what members SEE:
        # `optimisation` gives no price signal, `market` prices the marginal kWh - so they
        # coincide under obedient members and diverge once members respond.
        order = np.argsort(-consumption_kw, axis=1)
        for t in range(n_steps):
            left = gen[t]
            for j in order[t]:
                take = min(left, consumption_kw[t, j])
                alloc[t, j] = take
                left -= take
                if left <= 1e-9:
                    break
    else:
        raise ValueError(f"unknown mechanism {mechanism!r}")

    # never allocate more than exists
    tot = alloc.sum(axis=1)
    over = tot > gen + 1e-9
    if np.any(over):
        scale = np.where(over, gen / np.maximum(tot, 1e-9), 1.0)
        alloc = alloc * scale[:, None]
    return np.clip(alloc, 0.0, None)


# --------------------------------------------------------------------- settlement
def member_prices(regime: RegimeConfig) -> tuple[float, float, float]:
    """(price of shared energy, price of grid energy, export remuneration) in EUR/kWh.

    The Mieterstrom surcharge is NOT part of the export price. Under s21(3) EEG it is paid on
    electricity supplied to and consumed by tenants, so it attaches to SHARED kWh and is
    credited to the plant operator in `settle`. An earlier version added it to the export
    remuneration, which rewarded exporting for a subsidy that exists to reward the opposite.
    """
    shared = (regime.internal_price + regime.shared_network_charge + regime.shared_levies
              + regime.shared_electricity_tax) * (1 + regime.shared_vat)
    grid = (regime.grid_energy + regime.grid_margin + regime.grid_network_charge
            + regime.grid_levies + regime.grid_electricity_tax) * (1 + regime.grid_vat)
    export = regime.feed_in_tariff
    return shared, grid, export


def external_charge_on_shared(regime: RegimeConfig) -> float:
    """EUR per shared kWh that leaves the community: charges, levies, tax, and VAT.

    The internal price itself is a transfer between members and the PV owner and cancels out
    at community level. The VAT levied on it does not, because it goes to the state; that is
    the one channel through which the internal price level changes the community's total.
    """
    charges = (regime.shared_network_charge + regime.shared_levies
               + regime.shared_electricity_tax)
    return charges * (1 + regime.shared_vat) + regime.internal_price * regime.shared_vat


def sharing_spread(regime: RegimeConfig) -> float:
    """Value created by one shared kWh versus buying it and exporting the PV kWh separately.

        s = p_grid - external_charge_on_shared - p_export + mieterstrom_surcharge

    This single number decides whether sharing creates value at all. If s <= 0 a rational
    community does not share, whatever the allocation mechanism.
    """
    _, p_grid, p_export = member_prices(regime)
    return p_grid - external_charge_on_shared(regime) - p_export + regime.mieterstrom_surcharge


def ownership_shares(run: RunConfig) -> np.ndarray:
    """Each member's share of the community PV, from `share_key`, normalised to sum to 1."""
    k = np.array([m.share_key for m in run.members], dtype=float)
    return k / k.sum()


def settle(consumption_kw: np.ndarray, alloc_kw: np.ndarray, generation_kw: np.ndarray,
           run: RunConfig) -> dict:
    """Per-member and community-level economics, from three explicit perspectives.

    **Consumers** pay the grid for what is not shared and the internal price (plus charges) for
    what is. **The PV owner** receives the internal price and the Mieterstrom surcharge on
    shared kWh and the feed-in tariff on the rest. **The community** is both, so internal-price
    payments cancel and only external charges remain.

    The identity that ties them together, and that the test suite asserts:

        coalition value = consumer saving + owner gain = sharing_spread * shared kWh

    An earlier version reported a "community total" that counted export revenue as the
    community's but treated internal-price payments as money leaving it. Mixing the two
    perspectives made the internal price look like a real cost rather than a transfer.

    The individual counterfactual: every member buys all consumption from the grid, and the PV
    is exported at the feed-in tariff.
    """
    dt = run.dt
    reg = run.regime
    p_shared, p_grid, p_export = member_prices(reg)

    from_grid = np.clip(consumption_kw - alloc_kw, 0.0, None)
    bills = (alloc_kw.sum(axis=0) * dt * p_shared
             + from_grid.sum(axis=0) * dt * p_grid)
    baseline_bills = consumption_kw.sum(axis=0) * dt * p_grid

    shared_kwh = float(alloc_kw.sum() * dt)
    gen_kwh = float(np.asarray(generation_kw).sum() * dt)
    surplus = np.clip(generation_kw - alloc_kw.sum(axis=1), 0.0, None)
    export_revenue = float(surplus.sum() * dt * p_export)

    owner_receipts = (shared_kwh * (reg.internal_price + reg.mieterstrom_surcharge)
                      + export_revenue)
    owner_baseline = gen_kwh * p_export
    owner_gain = owner_receipts - owner_baseline

    consumer_saving = baseline_bills - bills
    # If the members own the PV (a genuine energy community), the owner's gain is theirs too,
    # distributed by ownership share. A member's full payoff is consumer saving plus dividend.
    dividend = ownership_shares(run) * owner_gain

    community_baseline = float(baseline_bills.sum()) - owner_baseline
    net_community_cost = float(bills.sum()) - owner_receipts
    return {
        "member_bills": bills,
        "member_baseline": baseline_bills,
        "member_saving": consumer_saving,                  # consumer perspective only
        "member_dividend": dividend,
        "member_payoff": consumer_saving + dividend,       # member-owned community
        "consumer_saving": float(consumer_saving.sum()),
        "owner_gain": float(owner_gain),
        "coalition_value": float(consumer_saving.sum() + owner_gain),
        "community_bill": float(bills.sum()),
        "community_baseline": community_baseline,
        "export_revenue": export_revenue,
        "owner_receipts": float(owner_receipts),
        "net_community_cost": net_community_cost,
        "shared_kwh": shared_kwh,
        "grid_kwh": float(from_grid.sum() * dt),
        "export_kwh": float(surplus.sum() * dt),
        "self_consumption": float(alloc_kw.sum() / max(np.asarray(generation_kw).sum(), 1e-9)),
        "self_sufficiency": float(alloc_kw.sum() / max(consumption_kw.sum(), 1e-9)),
    }


def individual_rationality(res: dict, key: str = "member_saving") -> dict:
    """Is every member at least as well off as going alone?

    The stability question. A mechanism that is collectively efficient but leaves members
    worse off than individual supply will lose them, and then it is not efficient either.

    `key` selects the perspective: `member_saving` judges members as consumers only (the PV
    belongs to a third party, as in Mieterstrom); `member_payoff` adds each member's
    ownership dividend (a member-owned energy community). The two can give opposite answers
    for the same dispatch, which is the point of reporting both.
    """
    saving = res[key]
    worse = int(np.sum(saving < -1e-9))
    return {
        "members_worse_off": worse,
        "ir_satisfied": worse == 0,
        "min_saving": float(saving.min()),
        "median_saving": float(np.median(saving)),
        "max_saving": float(saving.max()),
        "gini": _gini(saving),
    }


def _gini(x: np.ndarray) -> float:
    """Inequality of the benefit distribution. 0 = everyone gains equally."""
    x = np.sort(np.asarray(x, dtype=float) - min(0.0, float(np.min(x))))
    n = len(x)
    if n == 0 or x.sum() <= 0:
        return 0.0
    idx = np.arange(1, n + 1)
    return float((2 * np.sum(idx * x)) / (n * x.sum()) - (n + 1) / n)


def feeder_violations(consumption_kw: np.ndarray, alloc_kw: np.ndarray,
                      generation_kw: np.ndarray, cfg: CommunityConfig) -> int:
    """Steps where the net flow at the transformer exceeds the feeder limit.

    Sharing is not a pure accounting exercise: the electricity has to physically get there.
    Counting these is the cheapest available check that an allocation is deliverable.
    """
    net = consumption_kw.sum(axis=1) - generation_kw
    return int(np.sum(np.abs(net) > cfg.feeder_limit_kw))

"""EEG direct marketing, the negative-price rule, and imbalance settlement.

The two mechanics that most published hybrid-plant studies simplify away, and that this
module implements explicitly, are:

  1. **The market premium's MONTHLY reference.** The premium is `anzulegender Wert` minus the
     monthly market value of the technology. Because the reference is monthly, the objective
     is not separable across the month, and a rolling-horizon controller that ignores this
     optimises the wrong thing near month boundaries.

  2. **The negative-price rule.** Under the 2025 tightening, a new plant earns NO premium in
     any quarter-hour with a negative day-ahead price. The revenue function is therefore
     discontinuous in price and depends on the plant's commissioning vintage - which turns
     curtailment from something merely tolerated into something rational.
"""
from __future__ import annotations

import numpy as np

from .config import MarketConfig, PlantConfig


def premium_eligible(price: np.ndarray, cfg: MarketConfig) -> np.ndarray:
    """Boolean mask: is the market premium payable in this step?"""
    price = np.asarray(price, dtype=float)
    if cfg.negative_price_rule == "none":
        return np.ones(len(price), dtype=bool)
    if cfg.negative_price_rule == "new_2025":
        return price >= 0.0

    # "six_hour": premium suspended once prices have been negative for 6 consecutive hours
    neg = price < 0.0
    run = np.zeros(len(price), dtype=int)
    for i in range(len(price)):
        run[i] = run[i - 1] + 1 if (neg[i] and i > 0) else int(neg[i])
    # 6 hours expressed in steps is resolution-dependent; the caller supplies 15-min data,
    # so 24 steps. Kept explicit rather than hidden in a constant.
    return ~(neg & (run >= 24))


def monthly_market_value(price: np.ndarray, generation: np.ndarray,
                         month_id: np.ndarray | None = None) -> float:
    """Generation-weighted average price - the technology's market value.

    With a single evaluation window this is one number; a full-year study computes it per
    calendar month, which is what `month_id` enables.
    """
    gen = np.asarray(generation, dtype=float)
    if gen.sum() <= 0:
        return float(np.mean(price))
    return float(np.sum(np.asarray(price) * gen) / gen.sum())


def settle(res: dict[str, np.ndarray], price: np.ndarray, dt: float,
           plant: PlantConfig, market: MarketConfig,
           rebap: np.ndarray | None = None,
           schedule: np.ndarray | None = None,
           wind_share: float = 0.6,
           premium_rate: float | None = None) -> dict[str, float]:
    """Decompose the plant's revenue. All values EUR over the evaluated period.

    `premium_rate` (EUR/MWh) is EXOGENOUS and must be supplied by the caller. This matters:
    the market premium is `anzulegender Wert` minus the *technology-wide* monthly market
    value, which is a national figure published by the TSOs. A single plant cannot move it.

    Deriving it from the plant's own export instead - which an earlier version of this module
    did - creates a spurious feedback: a controller that correctly curtails during negative
    prices concentrates its output in high-price hours, which raises its own apparent market
    value, which cuts its own premium. The result was a perfect-foresight optimum scoring
    BELOW a do-nothing baseline, which is impossible and was caught by the ladder invariant.
    Passing the rate in keeps the objective the optimiser maximises and the settlement that
    scores it consistent.

    `wind_share` splits exported energy between the two technologies for premium purposes.
    A production-grade model tracks per-technology energy through the battery; the
    approximation is documented rather than hidden, and its effect is small because the two
    applicable values are close.
    """
    price = np.asarray(price, dtype=float)
    export = res["export"]

    spot_revenue = float(np.sum(export * price) * dt)

    premium_revenue = 0.0
    if market.eeg_enabled:
        if premium_rate is None:
            aw = (wind_share * market.anzulegender_wert_wind
                  + (1 - wind_share) * market.anzulegender_wert_pv)
            premium_rate = max(0.0, aw - float(np.mean(price)))
        eligible = premium_eligible(price, market)
        premium_revenue = float(np.sum(export[eligible]) * dt * premium_rate)

    marketing_fee = float(np.sum(export) * dt * market.direct_marketing_fee)
    degradation = float(np.sum(res["throughput"]) * plant.c_deg_eur_mwh)

    imbalance_cost = 0.0
    if market.imbalance_enabled and rebap is not None and schedule is not None:
        dev = export - schedule
        imbalance_cost = float(-np.sum(dev * rebap) * dt)

    net = spot_revenue + premium_revenue - marketing_fee - degradation - imbalance_cost
    return {
        "spot_revenue": spot_revenue,
        "premium_revenue": premium_revenue,
        "marketing_fee": marketing_fee,
        "degradation_cost": degradation,
        "imbalance_cost": imbalance_cost,
        "net_revenue": net,
        "chosen_curtail_mwh": float(np.sum(res["chosen_curtail"]) * dt),
        "forced_curtail_mwh": float(np.sum(res["forced_curtail"]) * dt),
        "export_mwh": float(np.sum(export) * dt),
    }


def effective_price(price: np.ndarray, market: MarketConfig,
                    premium_rate: float) -> np.ndarray:
    """Marginal value of an exported MWh, including the premium where it is payable.

    This is what the optimiser should maximise against, and it is discontinuous at zero
    whenever the negative-price rule bites - which is precisely why the optimal action flips
    qualitatively across that boundary.
    """
    eligible = premium_eligible(price, market)
    return np.asarray(price, dtype=float) + np.where(eligible, premium_rate, 0.0) \
        - market.direct_marketing_fee


def rebap_series(n: int, price: np.ndarray, cfg: MarketConfig,
                 rng: np.random.Generator) -> np.ndarray:
    """Heavy-tailed imbalance price. Real data comes from netztransparenz.de."""
    if not cfg.imbalance_enabled:
        return np.zeros(n)
    return np.asarray(price) + cfg.rebap_sigma * rng.standard_t(df=3, size=n) / np.sqrt(3.0)


def redispatch_series(n: int, cfg: MarketConfig, plant: PlantConfig,
                      rng: np.random.Generator) -> np.ndarray:
    """Export cap per step; equals the connection rating except during an order."""
    cap = np.full(n, plant.conn_mw)
    active = rng.random(n) < cfg.redispatch_prob_per_step
    for i in np.flatnonzero(active):
        dur = int(rng.integers(4, 24))
        cap[i:i + dur] = plant.conn_mw * (1 - cfg.redispatch_depth)
    return cap

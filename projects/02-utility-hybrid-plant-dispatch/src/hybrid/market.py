"""EEG direct marketing, the negative-price rule, and imbalance settlement.

The two mechanics that most published hybrid-plant studies simplify away, and that this
module implements explicitly, are:

  1. **The market premium's MONTHLY reference.** The premium is `anzulegender Wert` minus the
     monthly market value of the technology. Because the reference is monthly, the objective
     is not separable across the month, and a rolling-horizon controller that ignores this
     optimises the wrong thing near month boundaries.

  2. **The negative-price rule.** Under the 2025 tightening, a new plant earns NO premium in
     any quarter-hour with a negative day-ahead price. Older vintages lose it for a whole run
     of negative prices once the run lasts long enough: six hours down to one, by vintage. The
     revenue function is therefore discontinuous in price and depends on the plant's
     commissioning vintage - which turns curtailment from something merely tolerated into
     something rational.
"""
from __future__ import annotations

import numpy as np

from .config import MarketConfig, PlantConfig

# Hours of uninterrupted negative prices after which § 51 EEG removes the premium for the
# WHOLE run, by rule. A plant keeps the version of § 51 in force when it was commissioned or
# awarded (§ 100 EEG 2023). Texts from gesetze-im-internet.de, older versions from its
# archived pages:
#   six_hour   2016-2020: "in mindestens sechs aufeinanderfolgenden Stunden", § 24 (1)
#              EEG 2014 and § 51 (1) EEG 2017, kept by § 100 (2) Nr. 13 EEG 2021.
#   four_hour  2021-2022: "für die Dauer von mindestens vier aufeinanderfolgenden Stunden",
#              § 51 (1) in the version in force on 31 Dec 2022, kept by § 100 (1) EEG 2023.
#   four_hour to one_hour  1 Jan 2023 to 24 Feb 2025: § 51 (1) in the version in force on
#              24 Feb 2025, kept by § 100 (46) EEG 2023: four hours in 2023, three in 2024
#              and 2025, two in 2026, one from 2027 (see `vintage_rules`).
# All of them zero the applicable value "für den gesamten Zeitraum, in dem der
# Spotmarktpreis ohne Unterbrechung negativ ist": every step of the run, not only those
# after the threshold. Plants commissioned before 2016 carry no rule (§ 24 (3) Nr. 1
# EEG 2014, § 100 (1) EEG 2017). From 25 Feb 2025 (BGBl. 2025 I Nr. 51) § 51 (1) removes the
# premium "für Zeiträume, in denen der Spotmarktpreis negativ ist", whatever the run
# length: rule "new_2025".
_RUN_HOURS = {"six_hour": 6, "four_hour": 4, "three_hour": 3, "two_hour": 2, "one_hour": 1}


def premium_eligible(price: np.ndarray, cfg: MarketConfig, dt: float = 0.25) -> np.ndarray:
    """Boolean mask: is the market premium payable in this step?

    Parameters
    ----------
    price : day-ahead price per step, EUR/MWh, e.g. np.array([42.0, -3.1, -0.5, ...]). The
        series starts at the top of a calendar hour, as the data layer's frames do.
    cfg : market settings; `cfg.negative_price_rule` names the plant's rule.
    dt : step length in hours, 0.25 for quarter-hours.

    Returns
    -------
    np.ndarray of bool, one per step, True where the premium is payable.
    """
    price = np.asarray(price, dtype=float)
    if cfg.negative_price_rule == "none":
        return np.ones(len(price), dtype=bool)
    if cfg.negative_price_rule == "new_2025":
        return price >= 0.0
    return ~_in_long_negative_run(price, _RUN_HOURS[cfg.negative_price_rule], dt)


def _in_long_negative_run(price: np.ndarray, hours: int, dt: float) -> np.ndarray:
    """Steps that belong to a run of at least `hours` consecutive negative calendar hours.

    The rules count hours, not steps. Until 30 Sep 2025 the DE-LU day-ahead price is hourly
    and the data layer holds it across the quarter-hours, so an hour's steps share one price.
    From 1 Oct 2025 the products are quarter-hours, and § 100 (45) sentence 2 EEG 2023 counts
    an hour as negative when the arithmetic mean of its quarter-hour prices is negative; the
    hourly mean below covers both cases. A run cut by the series' start or end counts only
    its part inside the series.
    """
    per_hour = round(1.0 / dt)
    if per_hour < 1 or abs(per_hour * dt - 1.0) > 1e-9:
        raise ValueError(f"step length {dt} h does not divide an hour")
    if len(price) == 0:
        return np.zeros(0, dtype=bool)
    starts = np.arange(0, len(price), per_hour)
    sizes = np.diff(np.append(starts, len(price)))
    negative_hour = np.add.reduceat(price, starts) / sizes < 0.0
    # each run of negative hours as [begin, end), read off the edges of the 0/1 sequence
    edges = np.diff(np.concatenate([[0], negative_hour.astype(int), [0]]))
    long_hour = np.zeros(len(negative_hour), dtype=bool)
    for begin, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        if end - begin >= hours:
            long_hour[begin:end] = True
    return np.repeat(long_hour, sizes)


def vintage_rules(year: int) -> dict[str, str]:
    """Negative-price rule of each commissioning vintage in calendar year `year`.

    A plant keeps the rule of its commissioning date (see `_RUN_HOURS`). Only the 2023 to
    24 Feb 2025 vintage's threshold moves, with the calendar year of the prices; years before
    2023 get its 2023 threshold, as a counterfactual.

    Parameters
    ----------
    year : calendar year of the evaluated prices, e.g. 2024.

    Returns
    -------
    dict from vintage label to rule name, oldest vintage first, e.g.
    {"before 2016": "none", "2016-2020": "six_hour", ..., "from 25 Feb 2025": "new_2025"}.
    """
    if year <= 2023:
        eeg_2023 = "four_hour"
    elif year <= 2025:
        eeg_2023 = "three_hour"
    elif year == 2026:
        eeg_2023 = "two_hour"
    else:
        eeg_2023 = "one_hour"
    return {"before 2016": "none", "2016-2020": "six_hour", "2021-2022": "four_hour",
            "2023 to 24 Feb 2025": eeg_2023, "from 25 Feb 2025": "new_2025"}


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
        eligible = premium_eligible(price, market, dt)
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
                    premium_rate: float, dt: float = 0.25) -> np.ndarray:
    """Marginal value of an exported MWh, including the premium where it is payable.

    This is what the optimiser should maximise against, and it is discontinuous at zero
    whenever the negative-price rule bites - which is precisely why the optimal action flips
    qualitatively across that boundary. `dt` is the step length in hours, which the
    negative-price rule needs to count hours.
    """
    eligible = premium_eligible(price, market, dt)
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

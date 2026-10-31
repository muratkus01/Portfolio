"""EEG direct marketing, the negative-price rule, and imbalance settlement.

The two mechanics that most published hybrid-plant studies simplify away, and that this
module implements explicitly, are:

  1. **The market premium's reference value.** The premium is `anzulegender Wert` minus the
     technology's national market value over a calendar month, or over the calendar year for
     plants commissioned from 2023 on whose tender award, if any, also dates from 2023 on
     (EEG 2023 Anlage 1 Nr. 2). The value is only known once the period is over, so a
     controller that plans inside it must estimate it.

  2. **The negative-price rule.** Under the 2025 tightening, a new plant earns NO premium in
     any quarter-hour with a negative day-ahead price. Older vintages lose it for a whole run
     of negative prices once the run lasts long enough: six hours down to one, by vintage. The
     revenue function is therefore discontinuous in price and depends on the plant's vintage,
     dated by commissioning or by its tender - which turns curtailment from something merely
     tolerated into something rational.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MarketConfig, PlantConfig

# Hours of uninterrupted negative prices after which § 51 EEG removes the premium for the
# WHOLE run, by rule. § 100 keeps a plant on the version of § 51 that applied when it was
# commissioned or, from the 2021 cut-off on, when the tender that set its applicable value
# had its bid date (Gebotstermin): a plant tendered in 2020 and commissioned in 2022 keeps
# six hours, one tendered in 2024 and commissioned in 2026 keeps the rule of the 2023 to
# 24 Feb 2025 vintage (§ 100 (1) EEG 2021; § 100 (1) and (46) EEG 2023). Texts from
# gesetze-im-internet.de, older versions from its archived pages:
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
    """Negative-price rule of each vintage in calendar year `year`.

    A vintage is dated by the plant's commissioning or, for a tendered plant, by the bid date
    of its tender where § 100 says so (see `_RUN_HOURS`). Only the 2023 to 24 Feb 2025
    vintage's threshold moves, with the calendar year of the prices; years before 2023 get
    its 2023 threshold, as a counterfactual.

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


def monthly_market_value(price: np.ndarray, generation: np.ndarray) -> float:
    """Generation-weighted average price over one period: a technology's market value.

    `market_values` applies it per calendar month or year. (A `month_id` argument used to
    promise that and was ignored.)

    Parameters
    ----------
    price : day-ahead price per step, EUR/MWh, e.g. np.array([10.0, 100.0]).
    generation : the technology's feed-in on the same steps, any unit, e.g. [1.0, 0.0].

    Returns
    -------
    float, EUR/MWh; the plain mean price when there is no generation at all.
    """
    gen = np.asarray(generation, dtype=float)
    if gen.sum() <= 0:
        return float(np.mean(price))
    return float(np.sum(np.asarray(price) * gen) / gen.sum())


# Calendar periods a market value is computed over, as pandas period codes.
_PERIODS = {"month": "M", "year": "Y"}


def _period_labels(index: pd.DatetimeIndex, period: str) -> np.ndarray:
    """Europe/Berlin calendar month ("2024-01") or year ("2024") of each tz-aware timestamp."""
    if period not in _PERIODS:
        raise ValueError(f"market value period must be 'month' or 'year', not {period!r}")
    local = index.tz_convert("Europe/Berlin").tz_localize(None)
    return np.asarray(local.to_period(_PERIODS[period]).astype(str))


def _period_length(key: str, period: str) -> pd.Timedelta:
    """Length of the Berlin calendar month or year `key` ("2024-03" or "2024"), daylight saving
    time included: March 2024 is 743 hours."""
    p = pd.Period(key, freq=_PERIODS[period])
    return ((p + 1).start_time.tz_localize("Europe/Berlin")
            - p.start_time.tz_localize("Europe/Berlin"))


def market_values(price: pd.Series, feed_in: pd.DataFrame,
                  period: str = "month") -> pd.DataFrame:
    """Technology market values ("Marktwert") per Berlin calendar month or year.

    EEG 2023 Anlage 1 Nr. 3.3 (monthly value MW) and Nr. 4.3 (annual value JW): the day-ahead
    price of each quarter-hour weighted by the technology's national feed-in. Built from public
    generation data, this is a PROXY for the values the TSOs publish, which the premium is
    settled on. They weight by an online extrapolation that leaves curtailment out (Nr. 5.1);
    generation data that records curtailed output gives cheap, curtailed hours less weight, so
    the proxy should run high. No committed data measures the gap yet.

    Parameters
    ----------
    price : day-ahead price, EUR/MWh, on a tz-aware DatetimeIndex, e.g. a year of
        quarter-hours.
    feed_in : national feed-in per technology on the same index, one column each, e.g.
        columns "wind" and "pv". Only its shape within a period matters, so a capacity
        factor with one capacity over the period weights the price as the feed-in does.
    period : "month" or "year".

    Returns
    -------
    pd.DataFrame indexed by period label ("2024-01" or "2024"): one column per technology in
    EUR/MWh; "days", the Berlin calendar days the data covers in the period; and "complete",
    whether the data holds every step of the period. Only then is a value the realised one;
    otherwise it is the value of the period so far, which moves until the period ends.
    """
    labels = _period_labels(price.index, period)
    days = pd.Series(price.index.tz_convert("Europe/Berlin").tz_localize(None).normalize())
    # The data's step length; a day count alone would call a month whole on its last day.
    step = price.index[1] - price.index[0] if len(price) > 1 else None
    rows = {}
    for key in pd.unique(labels):
        m = labels == key
        rows[key] = {tech: monthly_market_value(price.to_numpy()[m],
                                                feed_in[tech].to_numpy()[m])
                     for tech in feed_in.columns}
        rows[key]["days"] = days[m].nunique()
        rows[key]["complete"] = (step is not None
                                 and int(m.sum()) * step == _period_length(key, period))
    return pd.DataFrame.from_dict(rows, orient="index")


def premium_table(index: pd.DatetimeIndex, generation: dict[str, np.ndarray],
                  values: pd.DataFrame, applicable: dict[str, float],
                  period: str = "month") -> pd.DataFrame:
    """Market premium per technology, and the plant's rate, for each period `index` touches.

    Per technology the premium is max(0, AW - MW) (Anlage 1 Nr. 3.1.2; Nr. 4.1.2 with JW). The
    law pays each technology on its own energy, and stored energy keeps the claim it would
    have had without the storage (§ 19 (3) EEG 2023). The model has one export and one
    battery for both, so it pays one rate per period: the premiums weighted by the plant's own
    available generation of each technology in the steps of `index` that fall in the period,
    so the rate depends on the evaluated window as well as on the period. That is an
    approximation, exact only when the mix is the same in every step. It misprices every step
    whose mix departs from the period's, the steps the negative-price rule acts on included,
    so it can misstate what the rule removes; and the optimiser cannot curtail one technology
    and keep the other.
    A per-step share would be no better behind a battery: it would pay discharged energy the
    mix of its discharge step and let the optimiser shift energy into the hours of the higher
    premium. Exact settlement needs each technology's export and a store that tracks whose
    energy it holds. Available rather than exported generation keeps the rate exogenous to
    the dispatch, as `settle` requires.

    Parameters
    ----------
    index : tz-aware timestamps of the evaluated steps, e.g. 1,920 quarter-hours of January.
    generation : the plant's available generation per technology on `index`, MW, e.g.
        {"wind": np.array([31.2, ...]), "pv": np.array([0.0, ...])}.
    values : `market_values(..., period)` over data that covers every period of `index`.
    applicable : anzulegender Wert per technology, EUR/MWh, e.g. {"wind": 73.5, "pv": 68.5}.
    period : "month" or "year", as `values` was computed.

    Returns
    -------
    pd.DataFrame indexed by period label, with "days" of market data, per technology the
    market value ("mw_wind") and premium ("premium_wind"), and the plant's "rate" (EUR/MWh).
    """
    labels = _period_labels(index, period)
    rows = {}
    for key in pd.unique(labels):
        m = labels == key
        row = {"days": values.loc[key, "days"]}
        energy = {}
        for tech, aw in applicable.items():
            row[f"mw_{tech}"] = values.loc[key, tech]
            row[f"premium_{tech}"] = max(0.0, aw - values.loc[key, tech])
            energy[tech] = float(np.sum(generation[tech][m]))
        total = sum(energy.values())
        # a period without any generation (never seen on national data) splits evenly
        share = {tech: e / total if total > 0 else 1 / len(energy) for tech, e in energy.items()}
        row["rate"] = sum(row[f"premium_{tech}"] * share[tech] for tech in energy)
        rows[key] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def premium_rates(index: pd.DatetimeIndex, table: pd.DataFrame,
                  period: str = "month", column: str = "rate") -> np.ndarray:
    """The plant's premium rate per step: each step gets its period's rate from the table.

    Parameters
    ----------
    index : tz-aware timestamps of the steps, as given to `premium_table`.
    table : output of `premium_table` for these steps.
    period : "month" or "year", as the table was built.
    column : the table's column to spread over the steps: the plant's "rate", or e.g. one
        technology's "premium_wind".

    Returns
    -------
    np.ndarray, EUR/MWh per step, for `run_ladder`, `effective_price` and `settle`.
    """
    return table[column].reindex(_period_labels(index, period)).to_numpy()


def planning_rates(index: pd.DatetimeIndex, monthly: pd.DataFrame) -> np.ndarray:
    """The plant's premium rate a controller can know in each step: the realised rate of the
    Berlin calendar month before the step's month.

    A market value is the actual mean of its whole period (Anlage 1 Nr. 3.3, 4.3), so it is
    known only once the period is over. A controller deciding inside a month therefore knows
    the previous month's at best, and this takes it as the estimate of the premium. For a
    plant paid against the annual value it is one estimate among several known as early: the
    previous year's annual value, or the mean of the year's months so far. B2 may plan on the
    realised value; B3, a deployable controller, plans on this.

    Parameters
    ----------
    index : tz-aware timestamps of the steps, e.g. a window from 1 January 2024.
    monthly : `premium_table(..., period="month")` over data that covers the month before
        each step's month, e.g. December 2023 for a window in January 2024.

    Returns
    -------
    np.ndarray, EUR/MWh per step: the "rate" of the month before the step's month.

    Raises
    ------
    ValueError when `monthly` lacks the month before one of the steps' months.
    """
    previous = (pd.PeriodIndex(_period_labels(index, "month"), freq="M") - 1).astype(str)
    rates = monthly["rate"].reindex(previous)
    missing = sorted(set(previous[rates.isna().to_numpy()]))
    if missing:
        raise ValueError(f"no realised premium rate for {missing}; the month before each "
                         "step's month must be in the table")
    return rates.to_numpy(dtype=float)


def settle(res: dict[str, np.ndarray], price: np.ndarray, dt: float,
           plant: PlantConfig, market: MarketConfig,
           rebap: np.ndarray | None = None,
           schedule: np.ndarray | None = None,
           premium_rate: float | np.ndarray | None = None) -> dict[str, float]:
    """Decompose the plant's revenue. All values EUR over the evaluated period.

    `premium_rate` (EUR/MWh) is EXOGENOUS and must be supplied by the caller: one rate, or
    one per step (`premium_rates`) when the window spans periods with different market
    values. This matters: the market premium is `anzulegender Wert` minus the
    *technology-wide* market value, which is a national figure published by the TSOs. A
    single plant cannot move it.

    Deriving it from the plant's own export instead - which an earlier version of this module
    did - creates a spurious feedback: a controller that correctly curtails during negative
    prices concentrates its output in high-price hours, which raises its own apparent market
    value, which cuts its own premium. The result was a perfect-foresight optimum scoring
    BELOW a do-nothing baseline, which is impossible and was caught by the ladder invariant.
    Passing the rate in keeps the objective the optimiser maximises and the settlement that
    scores it consistent. How the plant's export splits between its two technologies for the
    premium is settled in `premium_table`.

    The negative-price rule is applied at the step of export, while § 19 (3) EEG 2023 gives
    stored energy the claim of the step it was generated in; the settlement does not track
    energy through the battery. Energy charged while the premium is suspended therefore earns
    it here when it is discharged in an eligible step, where the law pays none, and energy
    stored in an eligible step earns none here when it is discharged during a suspension. For
    the usual pattern, charging during a suspension and selling later, this overstates what
    a battery recovers from the rule.

    Grid imports (`res["import"]`, MW per step; only with `grid_charging_allowed`) are bought
    on the day-ahead market the export is sold on, so they pay the day-ahead price. Grid fees
    and levies are not modelled. Sold again, that energy must not earn the premium, but this
    settlement cannot tell it from renewable energy in the battery; `baselines.run_ladder`
    refuses such a run.

    Parameters
    ----------
    res : dispatch per step, MW: "export", "import", "throughput" (MWh), "chosen_curtail" and
        "forced_curtail", e.g. the output of `plant.simulate`.
    price : day-ahead price per step, EUR/MWh. dt : step length in hours.
    plant, market : the plant's and the market's settings.
    rebap, schedule : imbalance price per step (EUR/MWh) and the committed export (MW); the
        deviation is settled only when both are given.
    premium_rate : market premium, EUR/MWh, one rate or one per step.

    Returns
    -------
    dict of EUR terms ("spot_revenue", "premium_revenue", "marketing_fee", "degradation_cost",
    "imbalance_cost", "import_cost", "net_revenue") and MWh totals.
    """
    price = np.asarray(price, dtype=float)
    export = res["export"]

    spot_revenue = float(np.sum(export * price) * dt)

    premium_revenue = 0.0
    if market.eeg_enabled:
        if premium_rate is None:
            # No fallback: the window's mean price, which this used to take, is not a market
            # value (see `market_values`).
            raise ValueError("premium_rate is required while EEG is enabled; "
                             "see market.premium_rates")
        rate = np.broadcast_to(np.asarray(premium_rate, dtype=float), export.shape)
        eligible = premium_eligible(price, market, dt)
        premium_revenue = float(np.sum(export[eligible] * rate[eligible]) * dt)

    marketing_fee = float(np.sum(export) * dt * market.direct_marketing_fee)
    degradation = float(np.sum(res["throughput"]) * plant.c_deg_eur_mwh)

    imbalance_cost = 0.0
    if market.imbalance_enabled and rebap is not None and schedule is not None:
        dev = export - schedule
        imbalance_cost = float(-np.sum(dev * rebap) * dt)

    import_cost = float(np.sum(res["import"] * price) * dt)

    net = (spot_revenue + premium_revenue - marketing_fee - degradation - imbalance_cost
           - import_cost)
    return {
        "spot_revenue": spot_revenue,
        "premium_revenue": premium_revenue,
        "marketing_fee": marketing_fee,
        "degradation_cost": degradation,
        "imbalance_cost": imbalance_cost,
        "import_cost": import_cost,
        "net_revenue": net,
        "chosen_curtail_mwh": float(np.sum(res["chosen_curtail"]) * dt),
        "forced_curtail_mwh": float(np.sum(res["forced_curtail"]) * dt),
        "export_mwh": float(np.sum(export) * dt),
    }


def effective_price(price: np.ndarray, market: MarketConfig,
                    premium_rate: float | np.ndarray, dt: float = 0.25) -> np.ndarray:
    """Marginal value of an exported MWh, including the premium where it is payable.

    This is what the optimiser should maximise against, and it is discontinuous at zero
    whenever the negative-price rule bites - which is precisely why the optimal action flips
    qualitatively across that boundary. `premium_rate` is one rate or one per step, as in
    `settle`; `dt` is the step length in hours, which the negative-price rule needs to count
    hours. With `market.eeg_enabled` off no premium is payable, as `settle` pays none.
    """
    if not market.eeg_enabled:
        return np.asarray(price, dtype=float) - market.direct_marketing_fee
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

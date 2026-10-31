"""Trading policies and settlement for the forecast-to-bid problem.

Every policy receives the decision stages built in `stages.py` (day-ahead first, then the
intraday stages in time order) and returns the final position plus one trade vector per
intraday stage. No policy picks an issue time itself.

  B0   day-ahead only       commit the day-ahead median, never correct
  B1   intraday to median   trade to the latest median at every intraday stage
  B3   deadband intraday    trade only the part of a revision that exceeds a band
                            proportional to the predictive spread
  NV   newsvendor fractile  commit a quantile chosen from imbalance-cost asymmetry; kept as
                            an ablation, see `nv_policy`
  REF  zero imbalance       commit the realised output; a reference, not a bound
  CEIL perfect speculation  perfect knowledge of output and prices; an information ceiling
"""
from __future__ import annotations

import numpy as np

from .config import MarketConfig, RunConfig
from .stages import Stage


# --------------------------------------------------------------------- market
def rebap_series(n: int, price: np.ndarray, imbalance_signal: np.ndarray,
                 cfg: MarketConfig, rng: np.random.Generator,
                 signal_scale: float | None = None) -> np.ndarray:
    """SYNTHETIC imbalance price for the parametric ablation: spot plus heavy-tailed noise.

    It is independent of the portfolio's own forecast error, which is why it cannot price
    the cost of an imbalance that runs with the system. Real results use the published reBAP.
    `signal_scale` normalises the signal; pass one estimated before the evaluation window,
    otherwise the whole series' spread is used and later data shapes earlier prices.
    """
    t = rng.standard_t(df=cfg.rebap_df, size=n) / np.sqrt(cfg.rebap_df / (cfg.rebap_df - 2))
    scale = np.std(imbalance_signal) if signal_scale is None else signal_scale
    sig = imbalance_signal / max(scale, 1e-9)
    mix = (cfg.rebap_sign_correlation * sig
           + np.sqrt(max(0.0, 1 - cfg.rebap_sign_correlation ** 2)) * t)
    return np.asarray(price, float) + cfg.rebap_sigma * mix


def trade_cost(volume: np.ndarray, cfg: MarketConfig, stage_idx: int,
               n_stages: int) -> np.ndarray:
    """Cost of an intraday correction: half-spread plus linear impact, in EUR/MWh terms.

    Widens near gate closure, which is the empirically robust feature of intraday liquidity
    and the reason a policy that waits for a better forecast pays for the privilege.
    """
    widen = 1.0 + (cfg.id_spread_widening_near_gate - 1.0) * (stage_idx / max(n_stages - 1, 1))
    v = np.abs(volume)
    return v * (cfg.id_spread_eur_mwh * widen + cfg.id_impact_eur_mwh_per_mw * v)


def settle(position: np.ndarray, trades: list[np.ndarray], truth: np.ndarray,
           price_da: np.ndarray, rebap: np.ndarray, price_id: np.ndarray,
           run: RunConfig, rebap_long: np.ndarray | None = None) -> dict[str, float]:
    """Settle a delivery period. `position` is the FINAL committed position per step.

    German imbalance settlement uses one price, the reBAP, for both directions. The
    published series has a separate value for long balance groups only in quarter-hours with
    a capacity-reserve call; pass it as `rebap_long` and it is applied where the portfolio
    is long, with `rebap` applied where it is short.
    """
    dt = run.dt
    cfg = run.market

    # day-ahead leg is the first committed position; intraday legs are the deltas
    da_position = position - sum(trades) if trades else position
    da_revenue = float(np.sum(da_position * price_da) * dt)

    id_revenue = 0.0
    id_cost = 0.0
    for k, tr in enumerate(trades):
        id_revenue += float(np.sum(tr * price_id) * dt)
        id_cost += float(np.sum(trade_cost(tr, cfg, k, len(trades))) * dt)

    deviation = truth - position
    price_imb = rebap if rebap_long is None else np.where(deviation > 0, rebap_long, rebap)
    imbalance = float(np.sum(deviation * price_imb) * dt)

    net = da_revenue + id_revenue - id_cost + imbalance
    produced = float(np.sum(truth) * dt)
    return {
        "da_revenue": da_revenue,
        "id_revenue": id_revenue,
        "id_cost": id_cost,
        "imbalance_settlement": imbalance,
        "net_revenue": net,
        "produced_mwh": produced,
        "eur_per_mwh": net / max(produced, 1e-9),
        "imbalance_mwh": float(np.sum(np.abs(deviation)) * dt),
        "traded_mwh": float(sum(np.sum(np.abs(t)) for t in trades) * dt),
    }


# --------------------------------------------------------------------- policies
def b0_day_ahead_only(stages: list[Stage]) -> tuple:
    """Commit the day-ahead median and never correct."""
    return stages[0].mean.copy(), []


def b1_point_intraday(stages: list[Stage]) -> tuple:
    """Correct to the updated median at each intraday stage. Industry practice."""
    pos = stages[0].mean.copy()
    trades = []
    for st in stages[1:]:
        trades.append(st.mean - pos)
        pos = st.mean.copy()
    return pos, trades


def newsvendor_quantile(cost_short: float, cost_long: float) -> float:
    """Critical fractile `cost_short / (cost_short + cost_long)` of a dual-price newsvendor."""
    return float(cost_short / max(cost_short + cost_long, 1e-9))


def nv_policy(stages: list[Stage], tau: float) -> tuple:
    """Commit the tau-quantile at day-ahead, then re-commit it at each intraday stage.

    ABLATION. The critical-fractile argument needs DIFFERENT prices for short and long
    imbalance. Germany settles both at one price, the reBAP, so for a price-taker the
    expected imbalance payment is linear in the position and no interior quantile is optimal;
    the legally required schedule is the best estimate. The policy is kept to show what the
    dual-price rule from the literature does in this market, with tau estimated on data
    BEFORE the evaluation window (`imbalance_cost_asymmetry`).
    """
    pos = stages[0].quantile(tau)
    trades = []
    for st in stages[1:]:
        target = st.quantile(tau)
        trades.append(target - pos)
        pos = target
    return pos, trades


def b3_deadband(stages: list[Stage], k: float = 0.25) -> tuple:
    """Trade only the part of each median revision that exceeds `k` times the spread.

    B1 trades the full revision at every stage and pays the spread on all of it. A revision
    much smaller than the remaining uncertainty is mostly noise, so B3 leaves it alone and
    trades only the excess over a band of `k` x (q90 - q10) of the stage's own forecast.
    This is a rule with one hand-set parameter, not an optimisation over scenarios.
    """
    pos = stages[0].mean.copy()
    trades = []
    for st in stages[1:]:
        delta = st.mean - pos
        moved = np.sign(delta) * np.maximum(np.abs(delta) - k * st.spread(), 0.0)
        trades.append(moved)
        pos = pos + moved
    return pos, trades


def b2_zero_imbalance(truth: np.ndarray) -> tuple:
    """Commit the realised generation exactly: a perfect forecast, zero imbalance.

    **This is NOT an upper bound on revenue.** Imbalance settlement is signed: when the
    imbalance price exceeds the day-ahead price, a long deviation is *paid*, so a policy that
    happens to deviate in the favourable direction can beat zero-imbalance operation. Zero
    imbalance is the operational reference; `b2_perfect_speculation` is the ceiling.
    """
    return np.asarray(truth, float).copy(), []


def b2_perfect_speculation(truth: np.ndarray, price_da: np.ndarray, rebap: np.ndarray,
                           capacity: float) -> tuple:
    """The information ceiling: perfect knowledge of generation AND the imbalance price.

    Revenue in a step is `pos*price_da + (truth - pos)*rebap`, linear in `pos` with slope
    `price_da - rebap`, so the optimum is bang-bang: commit everything when the day-ahead
    price is higher, nothing when the imbalance price is. No balance responsible party may
    do this (a BRP must schedule its best estimate), so it is reported as a ceiling only.
    """
    price_da = np.asarray(price_da, float)
    rebap = np.asarray(rebap, float)
    pos = np.where(price_da > rebap, capacity, 0.0)
    return pos, []


def imbalance_cost_asymmetry(rebap: np.ndarray, price_da: np.ndarray) -> tuple[float, float]:
    """Mean one-sided imbalance spreads, the inputs of the newsvendor fractile.

    cost_short: mean of (reBAP - day-ahead) where positive, what a shortfall costs per MWh
    cost_long : mean of (day-ahead - reBAP) where positive, what a surplus costs per MWh
    Estimate them on data before the evaluation window, never on the window itself.
    """
    spread = np.asarray(rebap) - np.asarray(price_da)
    return float(np.mean(np.clip(spread, 0, None))), float(np.mean(np.clip(-spread, 0, None)))

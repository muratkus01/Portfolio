"""Trading policies and settlement for the forecast-to-bid problem.

Policies, in increasing order of sophistication:

  B0  day-ahead only            commit the point forecast, take whatever imbalance results
  B1  point-forecast intraday   correct to the updated mean at each stage - INDUSTRY PRACTICE
  NV  newsvendor quantile       commit the cost-ratio quantile of the predictive distribution
  B2  perfect foresight         commit the truth; the ceiling
  B3  stochastic MPC            scenario-based correction with an explicit trading cost

The newsvendor policy is the sharp test in this project. Under an asymmetric imbalance cost
the optimal position is a QUANTILE of the predictive distribution, not its mean. If a learned
policy cannot beat the correct quantile of a well-calibrated forecast, the added complexity is
not earning its keep - and saying so is a result.
"""
from __future__ import annotations

import numpy as np

from .config import MarketConfig, RunConfig
from .forecast import ForecastStore


# --------------------------------------------------------------------- market
def rebap_series(n: int, price: np.ndarray, imbalance_signal: np.ndarray,
                 cfg: MarketConfig, rng: np.random.Generator) -> np.ndarray:
    """Imbalance price: heavy-tailed, and correlated with the system's own imbalance.

    The sign correlation is what makes deviation sometimes PROFITABLE - being short when the
    system is long is helping, and is paid for. A symmetric penalty model would delete the
    most interesting feature of the problem.
    """
    t = rng.standard_t(df=cfg.rebap_df, size=n) / np.sqrt(cfg.rebap_df / (cfg.rebap_df - 2))
    sig = imbalance_signal / max(np.std(imbalance_signal), 1e-9)
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
           run: RunConfig) -> dict[str, float]:
    """Settle a delivery period. `position` is the FINAL committed position per step."""
    dt = run.dt
    cfg = run.market
    da_revenue = float(np.sum(position * 0.0))          # placeholder, replaced below

    # day-ahead leg is the first committed position; intraday legs are the deltas
    da_position = position - sum(trades) if trades else position
    da_revenue = float(np.sum(da_position * price_da) * dt)

    id_revenue = 0.0
    id_cost = 0.0
    for k, tr in enumerate(trades):
        id_revenue += float(np.sum(tr * price_id) * dt)
        id_cost += float(np.sum(trade_cost(tr, cfg, k, len(trades))) * dt)

    deviation = truth - position
    imbalance = float(np.sum(deviation * rebap) * dt)

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
def _stage_leads(run: RunConfig) -> list[int]:
    return [int(round(h / run.dt)) for h in run.market.id_stages_h]


def b0_day_ahead_only(store: ForecastStore, n: int, run: RunConfig) -> tuple:
    """Commit the day-ahead point forecast and never correct."""
    lead = int(round(24 / run.dt))
    pos = np.array([store.at(max(0, t - lead), t)[0] for t in range(n)])
    return pos, []


def b1_point_intraday(store: ForecastStore, n: int, run: RunConfig) -> tuple:
    """Correct to the updated mean forecast at each intraday stage. Industry practice."""
    lead0 = int(round(24 / run.dt))
    pos = np.array([store.at(max(0, t - lead0), t)[0] for t in range(n)])
    trades = []
    for lead in _stage_leads(run):
        target = np.array([store.at(max(0, t - lead), t)[0] for t in range(n)])
        trades.append(target - pos)
        pos = target
    return pos, trades


def newsvendor_quantile(cost_short: float, cost_long: float) -> float:
    """Critical fractile: the optimal position is this quantile of the predictive law.

    Being SHORT (producing less than sold) costs `cost_short` per MWh; being LONG costs
    `cost_long`. The optimum is the `cost_short / (cost_short + cost_long)` quantile - above
    the median when shortage is dearer, which is the usual case.
    """
    return float(cost_short / max(cost_short + cost_long, 1e-9))


def nv_policy(store: ForecastStore, n: int, run: RunConfig,
              cost_short: float, cost_long: float) -> tuple:
    """Commit the cost-ratio quantile, then re-commit it at each intraday stage."""
    tau = newsvendor_quantile(cost_short, cost_long)
    lead0 = int(round(24 / run.dt))

    def q_at(issue: int, target: int) -> float:
        _, q = store.at(issue, target)
        return float(np.interp(tau, store.levels, q))

    pos = np.array([q_at(max(0, t - lead0), t) for t in range(n)])
    trades = []
    for lead in _stage_leads(run):
        target = np.array([q_at(max(0, t - lead), t) for t in range(n)])
        trades.append(target - pos)
        pos = target
    return pos, trades


def b2_zero_imbalance(truth: np.ndarray, run: RunConfig) -> tuple:
    """Commit the realised generation exactly: a perfect forecast, zero imbalance.

    **This is NOT an upper bound on revenue, and calling it one is a mistake this project
    made first time round.** Imbalance settlement is signed: when the imbalance price exceeds
    the day-ahead price, deviating is *paid*. A policy that happens to deviate in the
    favourable direction can therefore beat zero-imbalance operation, and in the 2024 data it
    does. Zero imbalance is the operationally desirable reference, not the money-maximising
    one.

    The genuine ceiling is `b2_perfect_speculation` below.
    """
    return np.asarray(truth, float).copy(), []


def b2_perfect_speculation(truth: np.ndarray, price_da: np.ndarray, rebap: np.ndarray,
                           run: RunConfig, capacity: float) -> tuple:
    """The true ceiling: perfect knowledge of generation AND the imbalance price.

    Revenue in a step is `pos*price_da + (truth - pos)*rebap`, linear in `pos` with slope
    `price_da - rebap`. With perfect knowledge the optimum is bang-bang: commit everything
    when the day-ahead price is higher, commit nothing when the imbalance price is.

    That is deliberate speculation against the imbalance mechanism, which no balance
    responsible party may actually do - a BRP is obliged to schedule its best estimate. So
    this rung is reported as an information-value ceiling only, never as a target. The gap
    between it and the zero-imbalance reference measures how much of the apparent "headroom"
    in this problem is really imbalance speculation rather than better forecasting.
    """
    price_da = np.asarray(price_da, float)
    rebap = np.asarray(rebap, float)
    pos = np.where(price_da > rebap, capacity, 0.0)
    return pos, []


def b3_stochastic_mpc(store: ForecastStore, n: int, run: RunConfig,
                      rebap_mean_short: float, rebap_mean_long: float,
                      deadband_k: float = 0.25) -> tuple:
    """Correct only when the forecast revision is large relative to remaining uncertainty.

    The essential difference from B1: B1 trades to the new mean at every stage, paying the
    spread on the full revision every time. B3 applies a DEADBAND proportional to the
    predictive spread, and trades only the part of the revision that exceeds it.

    The reasoning is that a revision much smaller than the remaining forecast uncertainty
    carries little information - it is mostly noise, and paying a spread to chase it destroys
    value. Scaling the deadband by the spread rather than using a fixed threshold is what
    makes this adaptive: it trades aggressively when the forecast has genuinely changed and
    sits still when it has merely wobbled.

    An earlier version compared a constant expected gain against a constant cost, which meant
    the condition was either always or never true - the deadband never triggered and B3 was
    numerically identical to the newsvendor policy. That is the failure mode this docstring
    exists to prevent recurring.
    """
    lead0 = int(round(24 / run.dt))
    tau = newsvendor_quantile(rebap_mean_short, rebap_mean_long)

    def q_at(issue: int, target: int) -> float:
        _, q = store.at(issue, target)
        return float(np.interp(tau, store.levels, q))

    pos = np.array([q_at(max(0, t - lead0), t) for t in range(n)])
    trades = []
    stages = _stage_leads(run)
    for k, lead in enumerate(stages):
        target = np.array([q_at(max(0, t - lead), t) for t in range(n)])
        spread = np.array([store.spread(max(0, t - lead), min(lead, store.max_lead - 1))
                           for t in range(n)])
        delta = target - pos
        band = deadband_k * spread
        # trade only the excess over the deadband, preserving the sign
        moved = np.sign(delta) * np.maximum(np.abs(delta) - band, 0.0)
        trades.append(moved)
        pos = pos + moved
    return pos, trades


def imbalance_cost_asymmetry(rebap: np.ndarray, price_da: np.ndarray) -> tuple[float, float]:
    """Empirical one-sided costs, used to set the newsvendor fractile.

    cost_short: what a shortfall costs per MWh (buy back at a high imbalance price)
    cost_long : what a surplus costs per MWh (sell at a low, possibly negative, price)
    """
    spread = np.asarray(rebap) - np.asarray(price_da)
    return float(np.mean(np.clip(spread, 0, None))), float(np.mean(np.clip(-spread, 0, None)))

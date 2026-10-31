"""Why the euro results look the way they do: three explanatory tables for reports/.

  error_alignment   splits each forecast's day-ahead result against the perfect forecast
                    into a bias part and an alignment part (below)
  quality_vs_value  does a better forecast earn more? rank correlation across the rungs
  liquidity_sweep   the intraday policies as a function of the half-spread they pay
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .config import RunConfig
from .evaluate import Ladder, daily_net, market_prices

INTRADAY_POLICIES = ("B0 day-ahead only", "B1 intraday to median", "B3 deadband intraday")
SPREAD_SWEEP = (0.0, 0.5, 1.0, 2.0, 3.0, 6.0)


def error_alignment(ladder: Ladder, df: pd.DataFrame, mask: np.ndarray,
                    run: RunConfig) -> pd.DataFrame:
    """B0's result against the perfect forecast, split exactly in two.

    Scheduling the day-ahead median f and settling the rest at reBAP earns the perfect
    forecast's result plus sum((y - f) * (reBAP - DA)) * dt. That sum is
      bias part       hours x mean(y - f) x mean(reBAP - DA)
      alignment part  hours x cov(y - f, reBAP - DA)
    The alignment part measures how the forecast's errors line up with the imbalance price:
    negative when the portfolio is long while the system is long, which reBAP punishes.
    """
    p = market_prices(df)
    y = df["truth_mw"].to_numpy(float)[mask]
    spread = (p["rebap"] - p["price_da"])[mask]
    hours = mask.sum() * run.dt
    rows = []
    for rung, stages in ladder.stages.items():
        err = y - stages[0].mean[mask]
        rows.append({
            "rung": rung, "hours": hours, "mean_spread_eur_mwh": spread.mean(),
            "mean_error_mw": err.mean(), "mae_mw": np.abs(err).mean(),
            "corr_error_spread": np.corrcoef(err, spread)[0, 1],
            "bias_part_eur": hours * err.mean() * spread.mean(),
            "alignment_part_eur": hours * np.mean((err - err.mean()) * (spread - spread.mean())),
            "b0_minus_ref_eur": np.sum(err * spread) * run.dt,
        })
    return pd.DataFrame(rows)


def quality_vs_value(scores: pd.DataFrame, totals: pd.DataFrame) -> pd.DataFrame:
    """Spearman correlation across rungs of forecast skill and net EUR, per policy.

    Skill is the negative day-ahead pinball loss, so +1 means the better forecast always
    earns more and -1 that it always earns less. Only the first LightGBM seed takes part,
    so one model is not counted three times.
    """
    one = ~scores["rung"].str.match(r"F3 seed [1-9]")
    skill = -scores[one & (scores["stage"] == "DA")].set_index("rung")["pinball"]
    rows = []
    for policy, group in totals[totals["rung"].isin(skill.index)].groupby("policy"):
        eur = group.set_index("rung")["net_revenue"].reindex(skill.index)
        rows.append({"policy": policy, "rungs": int(eur.notna().sum()),
                     "spearman_skill_vs_eur": skill.rank().corr(eur.rank())})
    return pd.DataFrame(rows)


def liquidity_sweep(ladder: Ladder, df: pd.DataFrame, mask: np.ndarray, run: RunConfig,
                    k_by_rung: dict[str, float], tau: float,
                    spreads=SPREAD_SWEEP) -> pd.DataFrame:
    """Net EUR of B0, B1 and B3 for every rung at each intraday half-spread."""
    rows = []
    for spread in spreads:
        swept = run.with_(market=replace(run.market, id_spread_eur_mwh=spread))
        totals, _ = daily_net(ladder, df, mask, swept, k_by_rung, tau)
        keep = totals[totals["policy"].isin(INTRADAY_POLICIES)]
        rows.append(keep[["rung", "policy", "net_revenue", "traded_mwh"]]
                    .assign(half_spread_eur_mwh=spread))
    out = pd.concat(rows, ignore_index=True)
    first = ["half_spread_eur_mwh", "rung", "policy"]
    return out[first + [c for c in out.columns if c not in first]]

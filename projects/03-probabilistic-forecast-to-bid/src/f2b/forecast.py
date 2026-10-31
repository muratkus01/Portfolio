"""Issue-time forecast store, the synthetic ablation that fills it, and probabilistic scoring.

Every forecast is materialised once, tagged with the issue step at which it would really have
been available. `ForecastStore.at` refuses a target before its issue, and `stages_from_store`
reads each decision stage at its own decision time (see `stages.py`), so the decision layer
cannot see a forecast from after its decision.

`build_store` is a PARAMETRIC NOISE MODEL, not a forecaster: the realised series plus
autocorrelated errors whose size is set by hand. It is kept as a labelled ablation only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ForecastConfig, RunConfig
from .stages import Stage, gate_closure_steps, intraday_decision_steps


def ar_noise(n: int, rng: np.random.Generator, rho: float) -> np.ndarray:
    z = rng.standard_normal(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * z[i]
    return z


@dataclass
class ForecastStore:
    """Quantile forecasts keyed by (issue step, lead).

    `mean[i, k]`       predictive mean for target `i + k`, issued at step `i`
    `quantiles[i,k,q]` predictive quantiles, same indexing
    `levels`           the quantile levels, ascending
    """
    mean: np.ndarray
    quantiles: np.ndarray
    levels: np.ndarray
    max_lead: int

    def at(self, issue: int, target: int) -> tuple[float, np.ndarray]:
        """Forecast for `target` as known at `issue`. Raises rather than answer another target."""
        if target < issue:
            raise ValueError(f"target {target} precedes issue {issue}")
        k = target - issue
        if k >= self.max_lead:
            raise ValueError(f"lead {k} steps is beyond the store's {self.max_lead}")
        return float(self.mean[issue, k]), self.quantiles[issue, k]


def build_store(truth: np.ndarray, cfg: ForecastConfig, dt: float,
                max_lead: int, rng: np.random.Generator,
                capacity: float, latency_steps: int = 4) -> ForecastStore:
    """Ablation: the realised series plus hand-sized noise, as an issue-time store.

    Errors are NESTED across lead times: the error for target t at lead k is the sum of
    independent increments over leads 0..k, each autocorrelated along t. Moving closer to
    delivery removes increments, so a revision is information and a later forecast is never
    worse in expectation, which is how real forecast updates behave. (An earlier version drew
    an independent error per lead, which made every revision fresh noise and rewarded any
    policy that averages revisions.)

    With `heteroscedastic`, the error of a target scales with how variable the series was up
    to that target, and the published spread scales with the variability the forecaster has
    already OBSERVED at the issue time, `latency_steps` + 1 steps back. The spread therefore
    carries information without using the future.
    """
    n = len(truth)
    levels = np.linspace(0.1, 0.9, cfg.n_quantiles)
    lead_h = np.arange(max_lead) * dt
    sigma = np.minimum(cfg.sigma_base + cfg.sigma_growth * lead_h, cfg.sigma_max) * capacity
    increments = np.stack([ar_noise(n, rng, cfg.rho) for _ in range(max_lead)], axis=1)
    error = np.cumsum(increments * np.sqrt(np.diff(sigma ** 2, prepend=0.0)), axis=1)

    if cfg.heteroscedastic:
        difficulty = 0.5 + 0.5 * _trailing_variability(truth)
        seen = np.concatenate([np.ones(latency_steps + 1), difficulty])[:n]
    else:
        difficulty = seen = np.ones(n)

    from .scipy_stub import (
        norm_ppf,  # vendored: this project installs from numpy+pandas alone
    )
    z = np.array([norm_ppf(lv) for lv in levels])

    mean = np.full((n, max_lead), np.nan)
    quantiles = np.full((n, max_lead, len(levels)), np.nan)
    for k in range(min(max_lead, n)):
        tgt = np.arange(k, n)                      # targets that exist, issued at tgt - k
        m = np.clip(truth[tgt] + difficulty[tgt] * error[tgt, k], 0.0, capacity)
        scale = sigma[k] * seen[tgt - k]
        mean[:n - k, k] = m
        quantiles[:n - k, k] = np.clip(m[:, None] + z[None, :] * scale[:, None], 0.0, capacity)
    return ForecastStore(mean=mean, quantiles=quantiles, levels=levels, max_lead=max_lead)


def _trailing_variability(truth: np.ndarray, window: int = 16) -> np.ndarray:
    """Mean absolute step change over the last `window` steps, relative to its own past.

    Both the window and the normalising mean end at each step: normalising by the mean of the
    whole series would let later data rescale earlier values.
    """
    vol = np.abs(np.diff(truth, prepend=truth[0]))
    vol = np.convolve(vol, np.ones(window) / window, mode="full")[:len(truth)]
    past_mean = np.cumsum(vol) / np.arange(1, len(vol) + 1)
    return vol / np.maximum(past_mean, 1e-9)


def stages_from_store(store: ForecastStore, index: pd.DatetimeIndex,
                      run: RunConfig) -> list[Stage]:
    """Day-ahead and intraday stages read from a dense store at their decision times."""
    decisions = [("DA", gate_closure_steps(index))]
    decisions += [(f"ID-{h:g}h", intraday_decision_steps(index, h, run.dt))
                  for h in run.market.id_stages_h]
    return [_read_stage(store, name, dec) for name, dec in decisions]


def _read_stage(store: ForecastStore, name: str, decision: np.ndarray) -> Stage:
    t = np.arange(len(decision))
    ok = decision >= 0
    issue = np.where(ok, decision, 0)
    lead = t - issue
    if np.any(ok & (lead >= store.max_lead)):
        raise ValueError(f"stage {name} needs leads up to {int(lead[ok].max())} steps, "
                         f"the store holds {store.max_lead}")
    lead = np.where(ok, lead, 0)
    mean = np.where(ok, store.mean[issue, lead], np.nan)
    quantiles = np.where(ok[:, None], store.quantiles[issue, lead], np.nan)
    return Stage(name=name, issue=np.where(ok, issue, -1), decision=decision,
                 mean=mean, quantiles=quantiles, levels=store.levels)


# --------------------------------------------------------------------- scoring
def pinball_loss(y: np.ndarray, q: np.ndarray, levels: np.ndarray) -> float:
    """Mean quantile loss across levels. Lower is better.

    With levels 0.1..0.9 this is the quantile score of those nine quantiles. It is NOT the
    CRPS: the CRPS integrates the loss over every level in (0, 1), and the tails beyond 0.1
    and 0.9 are not represented here.
    """
    y = np.asarray(y)[:, None]
    d = y - q
    return float(np.mean(np.maximum(levels * d, (levels - 1) * d)))


def calibration_error(y: np.ndarray, q: np.ndarray, levels: np.ndarray) -> float:
    """Reliability: mean |empirical coverage - nominal level| across quantile levels.

    For each level tau, a calibrated forecast puts the realisation below its tau-quantile
    exactly a fraction tau of the time. This averages the absolute deviation from that.
    0 is perfect; 0.5 is the worst possible.

    **Why not a PIT histogram.** Binning the probability integral transform is biased when
    the forecast is a finite quantile set covering only part of the unit interval: with
    levels 0.1 to 0.9, about 20 % of realisations necessarily fall outside the represented
    range and pile up at PIT 0 or 1. That version reported ~0.06 for a perfectly calibrated
    forecast at every lead time.
    """
    y = np.asarray(y, dtype=float)
    q = np.asarray(q, dtype=float)
    coverage = np.mean(y[:, None] <= q, axis=0)
    return float(np.mean(np.abs(coverage - levels)))


def score_stage(stage: Stage, truth: np.ndarray, mask: np.ndarray, dt: float) -> dict:
    """Point and probabilistic scores of one decision stage over the masked steps."""
    t = np.arange(len(truth))[mask]
    y, m, q = truth[mask], stage.mean[mask], stage.quantiles[mask]
    row = {
        "stage": stage.name,
        "mean_lead_h": float(np.mean(t - stage.issue[mask]) * dt),
        "MAE": float(np.mean(np.abs(y - m))),
        "RMSE": float(np.sqrt(np.mean((y - m) ** 2))),
        "bias": float(np.mean(m - y)),
        "pinball": pinball_loss(y, q, stage.levels),
        "calibration_error": calibration_error(y, q, stage.levels),
    }
    for lv, cov in zip(stage.levels, np.mean(y[:, None] <= q, axis=0)):
        row[f"coverage_q{round(100 * lv):02d}"] = float(cov)
    return row

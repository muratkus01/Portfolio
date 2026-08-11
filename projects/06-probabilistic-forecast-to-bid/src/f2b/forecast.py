"""Issue-time forecast store and probabilistic scoring.

The architectural centrepiece of this project. Every forecast is materialised ONCE, tagged
with the issue time at which it would really have been available, and every policy reads from
the same store. Look-ahead bias therefore becomes structurally impossible rather than a
matter of care - which is the single most common way an "RL beats MPC" result turns out to be
false.

`audit_no_lookahead` is the automated check that belongs in CI.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ForecastConfig


def ar_noise(n: int, rng: np.random.Generator, rho: float) -> np.ndarray:
    z = rng.standard_normal(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * z[i]
    return z


@dataclass
class ForecastStore:
    """Quantile forecasts keyed by (issue step, target step).

    `mean[i, k]`      predictive mean for target `i + k`, issued at step `i`
    `quantiles[i,k,q]` predictive quantiles, same indexing
    `levels`          the quantile levels, ascending
    """
    mean: np.ndarray
    quantiles: np.ndarray
    levels: np.ndarray
    max_lead: int

    def at(self, issue: int, target: int) -> tuple[float, np.ndarray]:
        """Forecast for `target` as known at `issue`. Raises on a look-ahead request."""
        if target < issue:
            raise ValueError(f"target {target} precedes issue {issue}")
        k = target - issue
        if k >= self.max_lead:
            k = self.max_lead - 1
        return float(self.mean[issue, k]), self.quantiles[issue, k]

    def path(self, issue: int, horizon: int) -> np.ndarray:
        """Predictive means for the next `horizon` steps, as known at `issue`."""
        h = min(horizon, self.max_lead)
        return self.mean[issue, :h]

    def spread(self, issue: int, lead: int) -> float:
        """Interquartile-ish spread - the observable uncertainty signal."""
        q = self.quantiles[issue, min(lead, self.max_lead - 1)]
        return float(q[-1] - q[0])


def build_store(truth: np.ndarray, cfg: ForecastConfig, dt: float,
                max_lead: int, rng: np.random.Generator,
                capacity: float) -> ForecastStore:
    """Generate a physically-plausible issue-time forecast store from the realised series.

    Error grows with lead time and is autocorrelated along the target axis. When
    `heteroscedastic` is set, the spread also scales with how variable the recent truth has
    been - so the predictive spread carries real information about the error magnitude, which
    is the property that makes a distributional policy worth having.
    """
    n = len(truth)
    levels = np.linspace(0.1, 0.9, cfg.n_quantiles)
    mean = np.empty((n, max_lead))
    quantiles = np.empty((n, max_lead, cfg.n_quantiles))

    # one autocorrelated error field per lead time
    err = np.stack([ar_noise(n, rng, cfg.rho) for _ in range(max_lead)], axis=1)

    # local variability of the truth: a proxy for "is this a hard situation to forecast?"
    vol = np.abs(np.diff(truth, prepend=truth[0]))
    vol = np.convolve(vol, np.ones(16) / 16, mode="same")
    vol = vol / max(vol.mean(), 1e-9)

    from .scipy_stub import norm_ppf   # vendored: this project installs from numpy+pandas alone

    for k in range(max_lead):
        lead_h = k * dt
        sigma_rel = min(cfg.sigma_base + cfg.sigma_growth * lead_h, cfg.sigma_max)
        scale = sigma_rel * capacity
        if cfg.heteroscedastic:
            scale = scale * (0.5 + 0.5 * vol)
        target = np.roll(truth, -k)
        target[max(0, n - k):] = truth[-1]
        m = np.clip(target + scale * err[:, k], 0.0, capacity)
        mean[:, k] = m
        for j, lv in enumerate(levels):
            quantiles[:, k, j] = np.clip(m + norm_ppf(lv) * scale, 0.0, capacity)

    return ForecastStore(mean=mean, quantiles=quantiles, levels=levels, max_lead=max_lead)


def audit_no_lookahead(store: ForecastStore, n: int, sample: int = 500,
                       rng: np.random.Generator | None = None) -> int:
    """Assert that no forecast can be read for a target that precedes its issue time.

    Returns the number of violations found; belongs in CI, where it must be zero.
    """
    rng = rng or np.random.default_rng(0)
    bad = 0
    for _ in range(sample):
        i = int(rng.integers(0, n))
        t = int(rng.integers(0, n))
        try:
            store.at(i, t)
            if t < i:
                bad += 1                     # should have raised
        except ValueError:
            if t >= i:
                bad += 1                     # should not have raised
    return bad


# --------------------------------------------------------------------- scoring
def pinball_loss(y: np.ndarray, q: np.ndarray, levels: np.ndarray) -> float:
    """Mean quantile loss across levels. Lower is better."""
    y = np.asarray(y)[:, None]
    d = y - q
    return float(np.mean(np.maximum(levels * d, (levels - 1) * d)))


def crps_from_quantiles(y: np.ndarray, q: np.ndarray, levels: np.ndarray) -> float:
    """CRPS approximated from a quantile representation.

    Equals twice the average pinball loss over uniformly spaced levels in the limit; the
    approximation is fine for the 9 levels used here and avoids a SciPy dependency.
    """
    return 2.0 * pinball_loss(y, q, levels)


def pit_values(y: np.ndarray, q: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Probability integral transform: where each realisation falls in its own forecast.

    A calibrated forecast gives a UNIFORM PIT histogram. Sharpness without calibration is
    worthless, so this is always reported alongside CRPS.
    """
    return np.array([float(np.interp(yi, qi, levels, left=0.0, right=1.0))
                     for yi, qi in zip(y, q)])


def calibration_error(y: np.ndarray, q: np.ndarray, levels: np.ndarray) -> float:
    """Reliability: mean |empirical coverage - nominal level| across quantile levels.

    For each level tau, a calibrated forecast puts the realisation below its tau-quantile
    exactly a fraction tau of the time. This averages the absolute deviation from that.
    0 is perfect; 0.5 is the worst possible.

    **Why not a PIT histogram.** The obvious implementation - bin the probability integral
    transform and measure deviation from uniform - is biased when the forecast is represented
    by a finite quantile set covering only part of the unit interval. With levels spanning
    0.1 to 0.9, about 20 % of realisations necessarily fall outside the represented range and
    pile up at PIT 0 or 1, so the histogram can never be uniform however well calibrated the
    forecast is. That version reported ~0.06 for a perfectly calibrated forecast at every lead
    time, which is how the bias was noticed: the "calibration error" did not vary with lead
    time at all.

    `pit_values` is kept for plotting reliability diagrams, where the same caveat applies and
    the endpoints should be read as censored rather than as evidence of miscalibration.
    """
    y = np.asarray(y, dtype=float)
    q = np.asarray(q, dtype=float)
    coverage = np.mean(y[:, None] <= q, axis=0)
    return float(np.mean(np.abs(coverage - levels)))


def score_store(store: ForecastStore, truth: np.ndarray, lead: int) -> dict[str, float]:
    """Score the store at a fixed lead time."""
    n = len(truth)
    idx = np.arange(0, n - lead)
    y = truth[idx + lead]
    q = store.quantiles[idx, min(lead, store.max_lead - 1)]
    m = store.mean[idx, min(lead, store.max_lead - 1)]
    return {
        "lead_steps": float(lead),
        "MAE": float(np.mean(np.abs(y - m))),
        "RMSE": float(np.sqrt(np.mean((y - m) ** 2))),
        "bias": float(np.mean(m - y)),
        "CRPS": crps_from_quantiles(y, q, store.levels),
        "pinball": pinball_loss(y, q, store.levels),
        "calibration_error": calibration_error(y, q, store.levels),
    }

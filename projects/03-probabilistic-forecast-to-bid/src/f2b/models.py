"""The forecast ladder, F0 climatology to F3 gradient boosting, plus the TSO benchmark F4.

Every rung predicts the portfolio capacity factor y = output / capacity at the nine levels
0.1..0.9 for one decision stage, behind one interface: `fit(X, y, times)` on training rows,
`predict(X, times)` on others. Predictions are sorted across levels (monotone rearrangement,
so quantiles never cross) and clipped to [0, 1] before they enter a `Stage`.

  F0  climatology   empirical quantiles by calendar month and local hour of the target
  F1  persistence   wind as last measured, PV as on the last measured day, plus residual
                    quantiles by local hour: the honest naive benchmark
  F2  linear        linear quantile regression on aggregate weather and measurement features
  F3  boosting      LightGBM quantile regression on all features, one model per level
  F4  TSO           the published TSO forecast plus residual quantiles; BENCHMARK only, it
                    may carry information from after the gate (see datakit)

F2 and F3 are wrapped in `Recalibrated`: fitted without the last days of their training
window, then shifted level by level so that those held-out days are covered as nominal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .stages import TZ_MARKET

LEVELS = np.linspace(0.1, 0.9, 9)

# Aggregates only: the linear rung shows how much is linear before any tree is fitted.
LINEAR_FEATURES = (
    "lead_h", "wind_on_power_proxy", "wind_on_ws_mean", "wind_off_power_proxy", "ghi_mean",
    "cloud_mean", "temp_mean", "cos_zenith", "wind_cf_obs", "wind_cf_obs_1h", "pv_cf_obs",
    "pv_cf_last_day", "hour_sin", "hour_cos", "doy_sin", "doy_cos",
)


def finish(q: np.ndarray) -> np.ndarray:
    """Monotone rearrangement and the physical range: sorted across levels, inside [0, 1]."""
    return np.clip(np.sort(q, axis=1), 0.0, 1.0)


def _local_hour(times: pd.DatetimeIndex) -> np.ndarray:
    return times.tz_convert(TZ_MARKET).hour.to_numpy()


class Climatology:
    """F0: quantiles of y by (calendar month, local hour) of the target, from training only."""
    name = "F0 climatology"

    def fit(self, X: pd.DataFrame, y: np.ndarray, times: pd.DatetimeIndex) -> Climatology:
        key = self._key(times)
        frame = pd.DataFrame({"key": key, "y": y})
        self.table = frame.groupby("key")["y"].quantile(LEVELS).unstack()
        self.fallback = np.quantile(y, LEVELS)
        return self

    def predict(self, X: pd.DataFrame, times: pd.DatetimeIndex) -> np.ndarray:
        q = self.table.reindex(self._key(times)).to_numpy(float, copy=True)
        missing = np.isnan(q).any(axis=1)
        q[missing] = self.fallback
        return finish(q)

    @staticmethod
    def _key(times: pd.DatetimeIndex) -> np.ndarray:
        return times.month.to_numpy() * 100 + _local_hour(times)


class ResidualQuantiles:
    """A point forecast plus empirical residual quantiles by local hour of the target.

    The base of F1 (persistence) and F4 (TSO forecast): `point(X)` gives the capacity-factor
    point forecast, the training residuals y - point give the spread.
    """
    name = "residual quantiles"

    def fit(self, X: pd.DataFrame, y: np.ndarray, times: pd.DatetimeIndex):
        resid = y - self.point(X)
        frame = pd.DataFrame({"h": _local_hour(times), "r": resid}).dropna()
        self.table = frame.groupby("h")["r"].quantile(LEVELS).unstack()
        return self

    def predict(self, X: pd.DataFrame, times: pd.DatetimeIndex) -> np.ndarray:
        offsets = self.table.reindex(_local_hour(times)).to_numpy(float)
        return finish(self.point(X)[:, None] + offsets)

    def point(self, X: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError


class Persistence(ResidualQuantiles):
    """F1: wind persists from the last measurement, PV repeats the last measured day."""
    name = "F1 persistence"

    def __init__(self, wind_share: float, pv_share: float):
        self.wind_share, self.pv_share = wind_share, pv_share

    def point(self, X: pd.DataFrame) -> np.ndarray:
        return (self.wind_share * X["wind_cf_obs"].to_numpy(float)
                + self.pv_share * X["pv_cf_last_day"].to_numpy(float))


class TsoForecast(ResidualQuantiles):
    """F4: the published TSO forecast of the fleet, read from column `tso_cf` of X."""
    name = "F4 TSO forecast"

    def point(self, X: pd.DataFrame) -> np.ndarray:
        return X["tso_cf"].to_numpy(float)


class LinearQuantile:
    """F2: one linear quantile regression per level on standardised aggregate features.

    The pinball loss is smoothed (softplus with width `eps`, in capacity-factor units) and
    minimised with L-BFGS. An exact linear-programming fit did not finish on 48k rows within
    five minutes on this machine; the smoothing changes the solution by far less than the
    sampling noise of the fit.
    """
    name = "F2 linear quantile"

    def __init__(self, features=LINEAR_FEATURES, eps: float = 0.005, l2: float = 1e-4):
        self.features, self.eps, self.l2 = list(features), eps, l2

    def fit(self, X: pd.DataFrame, y: np.ndarray, times: pd.DatetimeIndex,
            X_val: pd.DataFrame | None = None, y_val: np.ndarray | None = None) -> LinearQuantile:
        """`X_val`, `y_val` are accepted for a common interface and unused: no early stopping."""
        from scipy.optimize import minimize

        A = X[self.features].to_numpy(float)
        complete = np.isfinite(A).all(axis=1) & np.isfinite(y)
        A, y = A[complete], y[complete]
        self.mu, self.sd = A.mean(axis=0), A.std(axis=0) + 1e-9
        Z = np.column_stack([np.ones(len(A)), (A - self.mu) / self.sd])
        self.coef = []
        for tau in LEVELS:
            beta0 = np.zeros(Z.shape[1])
            beta0[0] = np.quantile(y, tau)
            res = minimize(self._loss, beta0, args=(Z, y, tau), jac=True, method="L-BFGS-B",
                           options={"maxiter": 500})
            self.coef.append(res.x)
        self.coef = np.array(self.coef)
        return self

    def predict(self, X: pd.DataFrame, times: pd.DatetimeIndex) -> np.ndarray:
        # a missing feature takes its training mean, i.e. contributes nothing
        Z = np.nan_to_num((X[self.features].to_numpy(float) - self.mu) / self.sd)
        return finish(np.column_stack([np.ones(len(Z)), Z]) @ self.coef.T)

    def _loss(self, beta: np.ndarray, Z: np.ndarray, y: np.ndarray, tau: float):
        r = y - Z @ beta
        s = -r / self.eps
        softplus = np.logaddexp(0.0, s)
        loss = np.mean(tau * r + self.eps * softplus) + self.l2 * np.sum(beta[1:] ** 2)
        sig = 0.5 * (1.0 + np.tanh(0.5 * s))           # sigmoid(s), overflow-safe
        grad = Z.T @ (sig - tau) / len(y)
        grad[1:] += 2 * self.l2 * beta[1:]
        return loss, grad


class GradientBoosting:
    """F3: LightGBM quantile regression, one model per level, on every feature.

    Each level stops early on the pinball loss of the held-out rows `X_val`, `y_val`, which
    must come from before the window that is scored; `best` records the iteration counts.
    """
    name = "F3 gradient boosting"

    def __init__(self, seed: int = 0, n_jobs: int = 4):
        self.seed, self.n_jobs = seed, n_jobs

    def params(self, tau: float) -> dict:
        return {"objective": "quantile", "alpha": float(tau), "learning_rate": 0.05,
                "num_leaves": 31, "min_data_in_leaf": 200, "bagging_fraction": 0.8,
                "bagging_freq": 1, "feature_fraction": 0.8, "lambda_l2": 1.0,
                "seed": self.seed, "num_threads": self.n_jobs, "verbose": -1}

    def fit(self, X: pd.DataFrame, y: np.ndarray, times: pd.DatetimeIndex,
            X_val: pd.DataFrame, y_val: np.ndarray) -> GradientBoosting:
        import lightgbm as lgb

        # LightGBM's native API (its scikit-learn wrapper would add scikit-learn as a
        # dependency) on plain arrays: the feature builder fixes the column order, and
        # LightGBM rejects some characters that the per-site feature names contain.
        train = lgb.Dataset(X.to_numpy(float), y, free_raw_data=False)
        valid = lgb.Dataset(X_val.to_numpy(float), y_val, reference=train)
        self.boosters, self.best = [], {}
        for tau in LEVELS:
            booster = lgb.train(self.params(tau), train, num_boost_round=3000,
                                valid_sets=[valid],
                                callbacks=[lgb.early_stopping(100, verbose=False)])
            self.best[f"{tau:.1f}"] = int(booster.best_iteration or booster.current_iteration())
            self.boosters.append(booster)
        return self

    def predict(self, X: pd.DataFrame, times: pd.DatetimeIndex) -> np.ndarray:
        A = X.to_numpy(float)
        return finish(np.column_stack([
            b.predict(A, num_iteration=b.best_iteration or None) for b in self.boosters]))


class Recalibrated:
    """A learned rung plus one shift per level, estimated on held-out calibration rows.

    The inner model is fitted (and, if it can, stopped early) without the calibration rows,
    then predicts them, and each level is shifted by the tau-quantile of its residuals there:
    on those rows exactly a share tau of the outcomes then falls below the tau-quantile. This
    removes a bias and widens an over-confident band using only data from before the window
    that is scored.
    """

    def __init__(self, model):
        self.model, self.name = model, model.name

    def fit(self, X: pd.DataFrame, y: np.ndarray, times: pd.DatetimeIndex,
            X_cal: pd.DataFrame, y_cal: np.ndarray, times_cal: pd.DatetimeIndex) -> Recalibrated:
        self.model.fit(X, y, times, X_cal, y_cal)
        q = self.model.predict(X_cal, times_cal)
        ok = np.isfinite(y_cal) & np.isfinite(q).all(axis=1)
        self.shift = np.array([np.quantile(y_cal[ok] - q[ok, j], tau)
                               for j, tau in enumerate(LEVELS)])
        return self

    def predict(self, X: pd.DataFrame, times: pd.DatetimeIndex) -> np.ndarray:
        return finish(self.model.predict(X, times) + self.shift)

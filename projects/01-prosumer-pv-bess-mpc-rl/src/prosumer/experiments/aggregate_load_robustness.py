"""Robustness of the aggregate load model (checks V5-V8).

V5 how uncertain the temperature slope is, honestly: 73 households and autocorrelated days
   make the naive standard error too small, so the headline uses a two-way bootstrap over
   households and 14-day blocks of days.
V6 how much the unknown HTW location matters: refit at every candidate state site.
V7 the out-of-sample test: fit on half the households without one week, predict the other
   half in that week, against BDEW H0 and against the first half's own same-day mean.
V8 how much of the remaining error is household noise and how much is shared by everyone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data import aggregate_load as al
from ..data.slp import STATES, state_holidays
from .aggregate_load_validation import EVENING, calendar_blocks, cv_rmse, prediction, row

BLOCK_DAYS = 14


# --------------------------------------------------------------------------- helpers
def newey_west_se(x: np.ndarray, resid: np.ndarray, lags: int) -> np.ndarray:
    """Heteroscedasticity- and autocorrelation-consistent standard errors (Bartlett kernel)."""
    xu = x * resid[:, None]
    s = xu.T @ xu
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        g = xu[lag:].T @ xu[:-lag]
        s += w * (g + g.T)
    inv = np.linalg.inv(x.T @ x)
    return np.sqrt(np.diag(inv @ s @ inv))


def moving_block_index(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Row indices of a moving-block bootstrap draw of length n, blocks of `block` rows."""
    starts = rng.integers(0, n - block + 1, size=-(-n // block))
    return np.concatenate([np.arange(s, s + block) for s in starts])[:n]


def _beta(x: np.ndarray, y: np.ndarray) -> float:
    coefs, *_ = np.linalg.lstsq(x, y, rcond=None)
    return float(coefs[-1])


# --------------------------------------------------------------------------- V5 slope
def _bootstrap(x, e_h, kind: str, n_boot: int, rng) -> np.ndarray:
    n, n_h = e_h.shape
    betas = np.empty(n_boot)
    for b in range(n_boot):
        hh = rng.integers(0, n_h, n_h) if kind in ("household", "two_way") else np.arange(n_h)
        rows = moving_block_index(n, BLOCK_DAYS, rng) if kind in ("time", "two_way") else np.arange(n)
        betas[b] = _beta(x[rows], e_h[rows][:, hh].mean(axis=1))
    return betas


def slope_uncertainty(ctx: dict, n_boot: int, seeds) -> tuple[pd.DataFrame, list]:
    """Temperature slope with naive, HAC and bootstrap standard errors (long format)."""
    keep = ctx["keep"]
    x = al.energy_design(ctx["dates"], ctx["national"], ctx["anomalies"][al.FIT_PROXY])[keep]
    e_h = (ctx["grid"].sum(axis=1) * 0.25)[keep]                 # (days, households)
    y = e_h.mean(axis=1)
    coefs, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coefs
    naive = np.sqrt(resid @ resid / (len(y) - x.shape[1]) * np.diag(np.linalg.inv(x.T @ x)))
    rows = [{"quantity": "beta", "method": "ols", "seed": None, "value": coefs[-1]},
            {"quantity": "se", "method": "naive", "seed": None, "value": naive[-1]}]
    rows += [{"quantity": "se", "method": f"hac_{lag}", "seed": None,
              "value": newey_west_se(x, resid, lag)[-1]} for lag in (7, 14)]
    out = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        for kind in ("household", "time", "two_way"):
            betas = _bootstrap(x, e_h, kind, n_boot, rng)
            rows.append({"quantity": "se", "method": kind, "seed": seed, "value": betas.std(ddof=1)})
            if kind == "two_way":
                for q in (2.5, 5.0, 95.0, 97.5):
                    rows.append({"quantity": f"p{q}", "method": kind, "seed": seed,
                                 "value": float(np.percentile(betas, q))})
                lo, hi = np.percentile(betas, [2.5, 97.5])
                out.append(row("V5 slope", "two-way 95 % CI excludes 0", f"seed {seed}",
                               float(hi), "<0", hi < 0 or lo > 0))
    acf = [float(np.corrcoef(resid[:-lag], resid[lag:])[0, 1]) for lag in range(1, 15)]
    rows += [{"quantity": f"acf_lag{lag}", "method": "residual", "seed": None, "value": v}
             for lag, v in enumerate(acf, start=1)]
    return pd.DataFrame(rows), out


# --------------------------------------------------------------------------- V6 proxy site
def proxy_sensitivity(ctx: dict, consistent: list) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """Refit at each state site; spread of 2024 per-state energy across consistent sites."""
    from .aggregate_load_fit import fit_from

    dates, keep, nat = ctx["dates"], ctx["keep"], ctx["national"]
    energy = ctx["mean"].sum(axis=1) * 0.25
    b7, b28 = calendar_blocks(len(dates), 7), calendar_blocks(len(dates), 28)
    models, rows = {}, []
    for site in STATES:
        models[site] = fit_from(ctx, site)
        x = al.energy_design(dates, nat, ctx["anomalies"][site])
        rows.append({"site": site, "holiday_consistent": site in consistent,
                     "beta": models[site].beta, "a_ref_kwh": models[site].a_ref,
                     "cv7": cv_rmse(x, energy, keep, b7), "cv28": cv_rmse(x, energy, keep, b28),
                     "mean_anomaly_2010": float(np.mean(ctx["anomalies"][site][keep]))})
    per_state = [_spread_2024(ctx, state, [models[p] for p in consistent]) for state in STATES]
    states = pd.DataFrame(per_state)
    ok_a, ok_d = states["annual_range_pct"].max() <= 1.0, states["max_daily_range_pct"].max() <= 3.0
    return pd.DataFrame(rows), states, [
        row("V6 proxy", "max annual energy range across consistent sites", "2024, 16 states",
            float(states["annual_range_pct"].max()), "<=1 %", ok_a),
        row("V6 proxy", "max range of the highest daily energy", "2024, 16 states",
            float(states["max_daily_range_pct"].max()), "<=3 %", ok_d)]


def _spread_2024(ctx: dict, state: str, models: list) -> dict:
    t = ctx["temps"][state]
    t = t[t.index.year == 2024]
    a = al.temperature_anomaly(t, ctx["normals"][state]).to_numpy()
    days = list(t.index.date)
    hol = state_holidays(state, 2024)
    units = np.array([al.daily_energy(m, days, hol, a)[0] / m.a_ref for m in models])
    annual, peak = units.sum(axis=1), units.max(axis=1)
    worst_day = ((units.max(axis=0) - units.min(axis=0)) / units.mean(axis=0)).max()
    return {"state": state, "annual_range_pct": 100 * (annual.max() - annual.min()) / annual.mean(),
            "max_daily_range_pct": 100 * (peak.max() - peak.min()) / peak.mean(),
            "worst_day_spread_pct": 100 * worst_day}


# --------------------------------------------------------------------------- V7 split half
def _predict_week(mean_a, x_e, x_s, types, train, test, h0) -> tuple[np.ndarray, np.ndarray]:
    energy_a = mean_a.sum(axis=1) * 0.25
    coefs, *_ = np.linalg.lstsq(x_e[train], energy_a[train], rcond=None)
    shares_a = mean_a / mean_a.sum(axis=1, keepdims=True)
    w = np.empty((test.sum(), al.N_SLOTS))
    for c in range(al.N_TYPES):
        g, *_ = np.linalg.lstsq(x_s[train & (types == c)], shares_a[train & (types == c)], rcond=None)
        sel = types[test] == c
        w[sel] = x_s[test][sel] @ g
    w /= w.sum(axis=1, keepdims=True)
    model_kw = (x_e[test] @ coefs)[:, None] * w / 0.25
    h0_kw = h0[test] * energy_a[train].sum() / (h0[train].sum() * 0.25)
    return model_kw, h0_kw


def _nrmse(pred: np.ndarray, truth: np.ndarray) -> tuple[float, float, float]:
    e = pred - truth
    daily = e.sum(axis=1) * 0.25
    return (float(np.sqrt(np.mean(e ** 2)) / truth.mean()),
            float(np.sqrt(np.mean(daily ** 2)) / (truth.sum(axis=1).mean() * 0.25)),
            float(np.sqrt(np.mean(e[:, EVENING] ** 2)) / truth[:, EVENING].mean()))


def split_half(ctx: dict, h0: np.ndarray, n_splits: int, seeds) -> tuple[pd.DataFrame, list]:
    """Unseen households in unseen weeks: model against H0 and the same-day mean."""
    dates, keep, grid = ctx["dates"], ctx["keep"], ctx["grid"]
    x_e = al.energy_design(dates, ctx["national"], ctx["anomalies"][al.FIT_PROXY])
    x_s = np.column_stack([np.ones(len(dates)), al.harmonics(dates, al.K_SHAPE)])
    types = al.shape_type(al.day_class(dates, ctx["national"]))
    weeks = calendar_blocks(len(dates), 7)
    rows, out = [], []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        for split in range(n_splits):
            perm = rng.permutation(grid.shape[2])
            mean_a = grid[:, :, perm[:len(perm) // 2]].mean(axis=2)
            mean_b = grid[:, :, perm[len(perm) // 2:]].mean(axis=2)
            pred = {k: np.full_like(mean_b, np.nan) for k in ("model", "h0", "same_day")}
            for w in np.unique(weeks):
                test, train = keep & (weeks == w), keep & (weeks != w)
                pred["model"][test], pred["h0"][test] = _predict_week(
                    mean_a, x_e, x_s, types, train, test, h0)
                pred["same_day"][test] = mean_a[test]
            res = {"seed": seed, "split": split}
            for k, p in pred.items():
                res[f"{k}_15min"], res[f"{k}_daily"], res[f"{k}_evening"] = _nrmse(p[keep], mean_b[keep])
            rows.append(res)
        df = pd.DataFrame([r for r in rows if r["seed"] == seed])
        wins_h0 = int((df["model_15min"] < df["h0_15min"]).sum())
        wins_sd = int((df["model_15min"] < df["same_day_15min"]).sum())
        ratio = float((df["model_15min"] / df["h0_15min"]).median())
        need = int(np.ceil(0.9 * n_splits))
        out += [row("V7 split-half", "splits where model beats H0 at 15 min", f"seed {seed}",
                    wins_h0, f">={need}/{n_splits}", wins_h0 >= need),
                row("V7 split-half", "splits where model beats same-day mean", f"seed {seed}",
                    wins_sd, f">={need}/{n_splits}", wins_sd >= need),
                row("V7 split-half", "median nRMSE ratio model/H0", f"seed {seed}", ratio,
                    "<=0.90", ratio <= 0.90)]
    return pd.DataFrame(rows), out


# --------------------------------------------------------------------------- V8 residuals
def _projected_ms(y: np.ndarray, x: np.ndarray) -> float:
    """Mean square of the residual of y on x. Not corrected for degrees of freedom, on purpose:
    the in-sample model residual it is compared with lost its fitted dimensions as well."""
    coefs, *_ = np.linalg.lstsq(x, y, rcond=None)
    r = y - x @ coefs
    return float(np.mean(r ** 2))


def residual_decomposition(ctx: dict, model: al.AggregateLoadModel, n_splits: int = 50,
                           seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the in-sample residual into household noise and a part shared by all homes.

    Half the difference of two random household halves cancels everything the households
    share. It still holds lasting differences between the two groups (level, weekday and
    seasonal pattern), which the model fitted on all 73 absorbs, so those are projected out
    with the model's own regressors first; what is left is day-to-day household noise of the
    full mean. What the residual has beyond that is common to everyone (weather the model does
    not see, events, the calendar).
    """
    dates, keep, grid, mean = ctx["dates"], ctx["keep"], ctx["grid"], ctx["mean"]
    anomaly = ctx["anomalies"][model.proxy]
    resid = (mean - prediction(model, dates, ctx["national"], anomaly))[keep]
    x_day = al.energy_design(dates, ctx["national"], anomaly)[keep]
    types = al.shape_type(al.day_class(dates, ctx["national"]))[keep]
    season = np.column_stack([np.ones(len(types)), al.harmonics(dates, al.K_SHAPE)[keep]])
    x_slot = np.column_stack([(types == c)[:, None] * season for c in range(al.N_TYPES)]
                             + [anomaly[keep]])
    rng = np.random.default_rng(seed)
    idio_q, idio_d = [], []
    for _ in range(n_splits):
        perm = rng.permutation(grid.shape[2])
        half = (grid[:, :, perm[:len(perm) // 2]].mean(axis=2)
                - grid[:, :, perm[len(perm) // 2:]].mean(axis=2))[keep] / 2.0
        idio_q.append(np.mean([_projected_ms(half[:, s], x_slot) for s in range(al.N_SLOTS)]))
        idio_d.append(_projected_ms(half.sum(axis=1) * 0.25, x_day))
    rows = []
    for scale, total_ms, idio_ms in (("15min_kw", np.mean(resid ** 2), np.mean(idio_q)),
                                     ("daily_kwh", np.mean((resid.sum(axis=1) * 0.25) ** 2),
                                      np.mean(idio_d))):
        rows.append({"scale": scale, "total_rms": np.sqrt(total_ms),
                     "idiosyncratic_rms": np.sqrt(idio_ms),
                     "common_mode_rms": np.sqrt(max(total_ms - idio_ms, 0.0))})
    daily = resid.sum(axis=1) * 0.25
    kept = [d for d, k in zip(dates, keep) if k]
    top = np.argsort(-np.abs(daily))[:20]
    days = pd.DataFrame({"date": [kept[i] for i in top], "weekday": [kept[i].strftime("%a") for i in top],
                         "anomaly_k": anomaly[keep][top], "residual_kwh": daily[top],
                         "residual_pct": 100 * daily[top] / (mean[keep].sum(axis=1)[top] * 0.25)})
    return pd.DataFrame(rows), days


# --------------------------------------------------------------------------- orchestration
def run_robustness(ctx, model, consistent, h0, out, n_splits, n_boot, seeds) -> list:
    """Run V5-V8 and write their tables to `out`; return the summary rows."""
    summary = []
    table, rows = slope_uncertainty(ctx, n_boot, seeds)
    table.to_csv(out / "slope_uncertainty.csv", index=False)
    summary += rows
    sites, states, rows = proxy_sensitivity(ctx, consistent)
    sites.to_csv(out / "proxy_sensitivity.csv", index=False)
    states.to_csv(out / "proxy_sensitivity_states_2024.csv", index=False)
    summary += rows
    table, rows = split_half(ctx, h0, n_splits, seeds)
    table.to_csv(out / "split_half.csv", index=False)
    summary += rows
    table, days = residual_decomposition(ctx, model)
    table.to_csv(out / "residual_decomposition.csv", index=False)
    days.to_csv(out / "residual_days.csv", index=False)
    return summary

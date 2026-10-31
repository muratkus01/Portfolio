"""Backtest of the forecast ladder and the value of each rung in the market.

Two phases, split by TARGET time (`features.SPLITS`):

  validation  fit on train, score and value the validation window, and tune B3's band there
              (the only number tuned on the window it is reported for).
  test        fit on train + validation with that band frozen, then score and value the test
              window ONCE.

In both phases the learned rungs stop early and recalibrate on the last
`features.CALIBRATION_DAYS` of their own training window, never on the scored window.

Positions settle at the real day-ahead price, intraday trades at the ID AEP index (the day-
ahead price where the index is undefined) and imbalance at the published reBAP.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import RunConfig
from .features import SPLITS, build_features, fit_and_calibration_rows, split_rows
from .forecast import score_stage
from .models import (
    LEVELS,
    Climatology,
    GradientBoosting,
    LinearQuantile,
    Persistence,
    Recalibrated,
    TsoForecast,
)
from .policies import (
    b0_day_ahead_only,
    b1_point_intraday,
    b2_perfect_speculation,
    b2_zero_imbalance,
    b3_deadband,
    imbalance_cost_asymmetry,
    newsvendor_quantile,
    nv_policy,
    settle_steps,
)
from .stages import (
    TZ_MARKET,
    Stage,
    audit_stages,
    gate_closure_steps,
    intraday_decision_steps,
)

STAGE_TSO = {"DA": "tso_da_mw", "ID-12h": "tso_id_mw", "ID-6h": "tso_id_mw",
             "ID-2h": "tso_cur_mw", "ID-0.5h": "tso_cur_mw"}
DEADBAND_GRID = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0)


@dataclass
class Ladder:
    """Stage forecasts (DA first) per rung, and the F3 iteration counts per seed and stage."""
    stages: dict[str, list[Stage]]
    f3_iterations: dict[str, dict[str, dict[str, int]]]


def stage_decisions(index: pd.DatetimeIndex, run: RunConfig) -> dict[str, np.ndarray]:
    out = {"DA": gate_closure_steps(index)}
    for h in run.market.id_stages_h:
        out[f"ID-{h:g}h"] = intraday_decision_steps(index, h, run.dt)
    return out


def fit_ladder(df: pd.DataFrame, weather_q: pd.DataFrame, run: RunConfig, phase: str,
               seeds=(0, 1, 2), log=print) -> Ladder:
    """Fit every rung for every decision stage and predict the `phase` window."""
    cap = run.portfolio.wind_mw + run.portfolio.pv_mw
    y_all = df["truth_mw"].to_numpy(float) / cap
    train_end = SPLITS["validation"][1] if phase == "test" else None
    stages, iterations = defaultdict(list), defaultdict(dict)
    for name, dec in stage_decisions(df.index, run).items():
        X = build_features(df, weather_q, dec, run.dt)
        rows, times = X.index.to_numpy(), df.index[X.index.to_numpy()]
        y = y_all[rows]
        fit, cal = fit_and_calibration_rows(df.index, X.index, train_end)
        fit, cal = fit & np.isfinite(y), cal & np.isfinite(y)
        ev = split_rows(df.index, X.index, phase)
        Xt = X.assign(tso_cf=df[STAGE_TSO[name]].to_numpy(float)[rows] / cap)
        baselines = {"F0 climatology": Climatology(),
                     "F1 persistence": Persistence(run.portfolio.wind_mw / cap,
                                                   run.portfolio.pv_mw / cap),
                     "F4 TSO forecast": TsoForecast()}
        for rung, model in baselines.items():
            model.fit(Xt[fit | cal], y[fit | cal], times[fit | cal])
            q = model.predict(Xt[ev], times[ev])
            stages[rung].append(_stage(name, dec, rows[ev], q * cap, len(df)))
        learned = {"F2 linear quantile": LinearQuantile(),
                   **{f"F3 seed {s}": GradientBoosting(s) for s in seeds}}
        for rung, inner in learned.items():
            model = Recalibrated(inner).fit(X[fit], y[fit], times[fit],
                                            X[cal], y[cal], times[cal])
            if isinstance(inner, GradientBoosting):
                iterations[rung][name] = inner.best
            q = model.predict(X[ev], times[ev])
            stages[rung].append(_stage(name, dec, rows[ev], q * cap, len(df)))
        log(f"  stage {name}: {int(fit.sum())} fit and {int(cal.sum())} calibration rows, "
            f"{int(ev.sum())} {phase} rows", flush=True)
    return Ladder(dict(stages), dict(iterations))


def _stage(name: str, decision: np.ndarray, rows: np.ndarray, q_mw: np.ndarray,
           n: int) -> Stage:
    quantiles = np.full((n, len(LEVELS)), np.nan)
    quantiles[rows] = q_mw
    issue = np.full(n, -1)
    issue[rows] = decision[rows]
    median = quantiles[:, int(np.argmin(np.abs(LEVELS - 0.5)))].copy()
    return Stage(name=name, issue=issue, decision=decision, mean=median,
                 quantiles=quantiles, levels=LEVELS)


def save_ladder(ladder: Ladder, path) -> None:
    """The predicted rows of every stage forecast, so the market part can rerun without refits."""
    arrays, entries = {}, []
    for rung, stages in ladder.stages.items():
        for st in stages:
            rows = np.flatnonzero(np.isfinite(st.quantiles).all(axis=1))
            k = len(entries)
            arrays.update({f"rows{k}": rows, f"q{k}": st.quantiles[rows],
                           f"decision{k}": st.decision})
            entries.append([rung, st.name])
    np.savez_compressed(path, entries=np.array(entries), **arrays)


def load_ladder(path, n: int) -> Ladder:
    with np.load(path) as z:
        stages = defaultdict(list)
        for k, (rung, name) in enumerate(z["entries"]):
            stages[str(rung)].append(_stage(str(name), z[f"decision{k}"], z[f"rows{k}"],
                                            z[f"q{k}"], n))
    return Ladder(dict(stages), {})


def common_mask(ladder: Ladder, truth: np.ndarray) -> np.ndarray:
    """Steps every rung forecast at every stage, with a measured outcome."""
    ok = np.isfinite(truth)
    for stages in ladder.stages.values():
        for st in stages:
            ok &= np.isfinite(st.quantiles).all(axis=1)
    return ok


def score_ladder(ladder: Ladder, truth: np.ndarray, mask: np.ndarray, dt: float) -> pd.DataFrame:
    rows = []
    for rung, stages in ladder.stages.items():
        if audit_stages(stages):
            raise RuntimeError(f"{rung}: a forecast was read after its decision time")
        for st in stages:
            rows.append({"rung": rung, "steps": int(mask.sum()),
                         **score_stage(st, truth, mask, dt)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- market value
def market_prices(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Settlement prices; the ID AEP falls back to the day-ahead price where it is undefined."""
    price_da = df["price_da"].to_numpy(float)
    id_aep = df["id_aep"].to_numpy(float)
    rebap = df["rebap"].to_numpy(float)
    long = df["rebap_long"].to_numpy(float)
    return {"price_da": price_da, "price_id": np.where(np.isfinite(id_aep), id_aep, price_da),
            "rebap": rebap, "rebap_long": np.where(np.isfinite(long), long, rebap),
            "id_aep_missing": ~np.isfinite(id_aep)}


def newsvendor_tau(df: pd.DataFrame, phase: str) -> float:
    """Critical fractile from reBAP and day-ahead prices of the TRAINING window only."""
    end = SPLITS["validation"][1] if phase == "test" else SPLITS["train"][1]
    window = (df.index >= pd.Timestamp(SPLITS["train"][0], tz="UTC")) & (
        df.index < pd.Timestamp(end, tz="UTC"))
    ok = window & df["rebap"].notna().to_numpy() & df["price_da"].notna().to_numpy()
    tau = newsvendor_quantile(*imbalance_cost_asymmetry(df["rebap"].to_numpy()[ok],
                                                        df["price_da"].to_numpy()[ok]))
    return float(np.clip(tau, LEVELS[0], LEVELS[-1]))


def policy_positions(stages: list[Stage], k: float, tau: float) -> dict[str, tuple]:
    return {"B0 day-ahead only": b0_day_ahead_only(stages),
            "B1 intraday to median": b1_point_intraday(stages),
            "B3 deadband intraday": b3_deadband(stages, k),
            "NV newsvendor fractile": nv_policy(stages, tau)}


def daily_net(ladder: Ladder, df: pd.DataFrame, mask: np.ndarray, run: RunConfig,
              k_by_rung: dict[str, float], tau: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Settlement totals per (rung, policy) and net EUR per local delivery day."""
    p = market_prices(df)
    truth = df["truth_mw"].to_numpy(float)
    cap = run.portfolio.wind_mw + run.portfolio.pv_mw
    m = mask
    day = df.index[m].tz_convert(TZ_MARKET).date
    totals, daily = [], {}

    def book(rung: str, policy: str, pos: np.ndarray, trades: list[np.ndarray]) -> None:
        legs = settle_steps(pos[m], [t[m] for t in trades], truth[m], p["price_da"][m],
                            p["rebap"][m], p["price_id"][m], run, p["rebap_long"][m])
        row = {k: float(v.sum()) for k, v in legs.items()}
        row.update(rung=rung, policy=policy,
                   imbalance_mwh=float(np.abs(truth[m] - pos[m]).sum() * run.dt),
                   traded_mwh=float(sum(np.abs(t[m]).sum() for t in trades) * run.dt))
        totals.append(row)
        daily[(rung, policy)] = pd.Series(legs["net_revenue"]).groupby(day).sum()

    for rung, stages in ladder.stages.items():
        for policy, (pos, trades) in policy_positions(stages, k_by_rung.get(rung, 0.25),
                                                      tau).items():
            book(rung, policy, pos, trades)
    book("perfect", "REF zero imbalance", *b2_zero_imbalance(truth))
    book("perfect", "CEIL perfect speculation",
         *b2_perfect_speculation(truth, p["price_da"], p["rebap"], cap))
    produced = float(truth[m].sum() * run.dt)
    frame = pd.DataFrame(totals)
    frame["eur_per_mwh"] = frame["net_revenue"] / produced
    first = ["rung", "policy", "net_revenue", "eur_per_mwh"]
    frame = frame[first + [c for c in frame.columns if c not in first]]
    return frame, pd.DataFrame(daily)


def tune_deadband(ladder: Ladder, df: pd.DataFrame, mask: np.ndarray, run: RunConfig,
                  tau: float) -> dict[str, float]:
    """B3's band per rung: the grid value with the highest net EUR on the window given."""
    best = {}
    for rung in ladder.stages:
        values = {}
        for k in DEADBAND_GRID:
            sub = Ladder({rung: ladder.stages[rung]}, {})
            totals, _ = daily_net(sub, df, mask, run, {rung: k}, tau)
            row = totals[(totals.rung == rung) & (totals.policy == "B3 deadband intraday")]
            values[k] = float(row["net_revenue"].iloc[0])
        best[rung] = max(values, key=values.get)
    return best


def value_of_forecast(daily: pd.DataFrame, baseline: str = "F0 climatology",
                      block_days: int = 7, n_boot: int = 2000, seed: int = 0) -> pd.DataFrame:
    """Each rung against the baseline rung, same policy: EUR difference with a 90 % CI.

    The interval is a moving-block bootstrap over delivery days (blocks of `block_days`), so
    weather spells that last several days are resampled together.
    """
    rng = np.random.default_rng(seed)
    days = len(daily)
    starts = rng.integers(0, max(days - block_days + 1, 1), size=(n_boot, -(-days // block_days)))
    idx = (starts[:, :, None] + np.arange(block_days)[None, None, :]).reshape(n_boot, -1)[:, :days]
    ref = daily[("perfect", "REF zero imbalance")]
    rows = []
    for rung, policy in daily.columns:
        if rung in ("perfect", baseline):
            continue
        base = daily[(baseline, policy)]
        diff = (daily[(rung, policy)] - base).to_numpy()
        boot = diff[idx].sum(axis=1)
        gap = float((ref - base).sum())
        rows.append({"rung": rung, "policy": policy, "gain_vs_F0_eur": float(diff.sum()),
                     "ci90_low": float(np.quantile(boot, 0.05)),
                     "ci90_high": float(np.quantile(boot, 0.95)),
                     "capture_of_F0_to_REF": float(diff.sum() / gap) if gap else np.nan})
    return pd.DataFrame(rows)

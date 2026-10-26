"""Evidence and model selection for the aggregate load model (checks V1-V4 and V9).

Each check writes at least one CSV under `reports/aggregate_load/`. Nine of the ten also
return pass/fail rows for `validation_summary.csv` (check, metric, variant, value, threshold,
passed), with thresholds fixed here before the numbers are seen; a pass/fail claim may be
quoted only if its row passed. V8 and the extra tables of V2, V4 and V6 are descriptive.
The robustness checks (V5-V8) live in `aggregate_load_robustness`, the per-state sanity
check (V10) in `aggregate_load_states`.
"""
from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from ..data import aggregate_load as al
from ..data.slp import STATES, state_holidays

EVENING = slice(68, 88)                     # 17:00-22:00 local
SPECIAL_DECEMBER = {(12, 24), (12, 31)}


# --------------------------------------------------------------------------- shared helpers
def row(check: str, metric: str, variant: str, value: float, threshold: str,
        passed: bool) -> dict:
    """One line of validation_summary.csv."""
    return {"check": check, "metric": metric, "variant": variant, "value": value,
            "threshold": threshold, "passed": bool(passed)}


def calendar_blocks(n_days: int, size: int) -> np.ndarray:
    """Consecutive blocks of `size` days; the remainder joins the last block (52 x 7, 13 x 28)."""
    return np.minimum(np.arange(n_days) // size, n_days // size - 1)


def cv_rmse(x: np.ndarray, y: np.ndarray, keep: np.ndarray, blocks: np.ndarray) -> float:
    """Leave-one-block-out RMSE of a linear model, over the kept days."""
    err = []
    for b in np.unique(blocks):
        test, train = keep & (blocks == b), keep & (blocks != b)
        if test.any():
            coefs, *_ = np.linalg.lstsq(x[train], y[train], rcond=None)
            err.append(y[test] - x[test] @ coefs)
    e = np.concatenate(err)
    return float(np.sqrt(np.mean(e ** 2)))


def h0_file_grid(dates, holidays: set[_dt.date]) -> np.ndarray:
    """BDEW H0 on the legal local clock, (n_days, 96) kW on the 1000 kWh/a basis."""
    from ..data.slp import _day_type, _dynamization, _season, fetch_h0_table

    table = fetch_h0_table(al.PROJECT / "data" / "raw" / "slp")
    w = np.array([table[f"{_season(d)}_{_day_type(d, holidays)}"].to_numpy() for d in dates])
    doy = pd.to_datetime(list(dates)).day_of_year.to_numpy()
    return w / 1000.0 * _dynamization(doy)[:, None]


def prediction(model: al.AggregateLoadModel, dates, holidays, anomaly) -> np.ndarray:
    """Model load in kW of the average household on a (n_days, 96) grid."""
    energy, _ = al.daily_energy(model, dates, holidays, anomaly)
    return energy[:, None] * al.day_shares(model, dates, holidays) / 0.25


# --------------------------------------------------------------------------- V1 clock
def half_max_time(day_kw: np.ndarray) -> float:
    """Hour at which the morning ramp crosses half way from the night minimum (02:00-05:00)
    to the morning peak (04:00-10:00), interpolated between quarter-hours."""
    night, peak = day_kw[8:20].min(), day_kw[16:40].max()
    level = (night + peak) / 2.0
    for s in range(16, 40):
        if day_kw[s] >= level:
            prev = day_kw[s - 1]
            frac = (level - prev) / (day_kw[s] - prev) if day_kw[s] != prev else 0.0
            return (s - 1 + frac) / 4.0
    return float("nan")


def _summer_time(d: _dt.date) -> bool:
    noon = pd.Timestamp(_dt.datetime(d.year, d.month, d.day, 12)).tz_localize(al.LOCAL_TZ)
    return bool(noon.dst())


def clock_check(mean: np.ndarray, dates, skip: set[_dt.date]) -> tuple[pd.DataFrame, list]:
    """Does the weekday morning ramp jump at the DST changes on the file clock?

    If the labels were MEZ and households lived on the legal clock, the ramp would jump one
    hour on the file clock; under that reading the legal-clock time is the file time plus
    one hour in summer. The step is measured over 21 days each side of each change.
    """
    t_file = np.array([half_max_time(r) for r in mean])
    summer = np.array([_summer_time(d) for d in dates], dtype=float)
    weekday = np.array([d.weekday() < 5 and d not in skip for d in dates])
    rows, out = [], []
    for name, edge in (("spring", _dt.date(2010, 3, 28)), ("autumn", _dt.date(2010, 10, 31))):
        gap = np.array([(d - edge).days for d in dates])
        before, after = weekday & (gap < 0) & (gap >= -21), weekday & (gap > 0) & (gap <= 21)
        for reading, t in (("file_clock", t_file), ("mez_reading", t_file + summer)):
            step = np.nanmean(t[after]) - np.nanmean(t[before])
            se = np.sqrt(np.nanvar(t[after], ddof=1) / after.sum()
                         + np.nanvar(t[before], ddof=1) / before.sum())
            rows.append({"transition": name, "reading": reading, "before_h": np.nanmean(t[before]),
                         "after_h": np.nanmean(t[after]), "step_h": step, "se_h": se})
            ok = abs(step) < 0.25 if reading == "file_clock" else abs(step) > 0.75
            out.append(row("V1 clock", f"morning ramp step {name}", reading, step,
                           "<0.25 h" if reading == "file_clock" else ">0.75 h", ok))
    return pd.DataFrame(rows), out


# --------------------------------------------------------------------------- V2 holidays
def templates(shares: np.ndarray, dates, i: int, ordinary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean share of the Sundays and of the ordinary same weekdays within +-21 days of day i."""
    gap = np.array([abs((d - dates[i]).days) for d in dates])
    wd = np.array([d.weekday() for d in dates])
    near = gap <= 21
    same = near & (wd == wd[i]) & ordinary & (np.arange(len(dates)) != i)
    return shares[near & (wd == 6)].mean(axis=0), shares[same].mean(axis=0)


def sunday_likeness(day_share: np.ndarray, tmpl: tuple[np.ndarray, np.ndarray]) -> float:
    """corr(day, Sunday template) minus corr(day, same-weekday template)."""
    return float(np.corrcoef(day_share, tmpl[0])[0, 1] - np.corrcoef(day_share, tmpl[1])[0, 1])


def _weekday_prediction(ctx: dict, model: al.AggregateLoadModel) -> tuple[np.ndarray, float]:
    """Daily energy of every day predicted as an ordinary weekday, and the residual SD."""
    dates, keep = ctx["dates"], ctx["keep"]
    energy = ctx["mean"].sum(axis=1) * 0.25
    a = np.clip(ctx["anomalies"][model.proxy], *model.anomaly_range)
    x = al.energy_design(dates, set(), a, classes=np.array([d.weekday() for d in dates]))
    fitted, _ = al.daily_energy(model, dates, ctx["national"], ctx["anomalies"][model.proxy])
    return x @ al.energy_coefs(model), float(np.std((energy - fitted)[keep], ddof=1))


def _holiday_days(ctx: dict) -> tuple[list, list, list, np.ndarray]:
    """Regional and nationwide weekday holidays, 24/31 Dec, and the ordinary-weekday mask."""
    dates = ctx["dates"]
    # The excluded days are the regional non-Sunday holidays plus the DST days, and clocks
    # always change on a Sunday.
    regional = sorted(d for d in ctx["excluded"] if d.weekday() != 6)
    national_wd = sorted(d for d in ctx["national"] if d.weekday() < 5)
    december = [d for d in dates if (d.month, d.day) in SPECIAL_DECEMBER]
    special = set(regional) | set(national_wd) | set(december) | ctx["excluded"]
    ordinary = np.array([d.weekday() < 5 and d not in special for d in dates])
    return regional, national_wd, december, ordinary


def holiday_check(ctx: dict, model: al.AggregateLoadModel) -> tuple[pd.DataFrame, list, list]:
    """Which 2010 holidays the households kept, and which states that is consistent with.

    A day counts as kept if its shape is more Sunday-like than every ordinary weekday of the
    year or its energy is at least 2 SD above its weekday prediction. How large a share of
    the homes must keep a holiday for this to see it is `holiday_detection_power`.
    """
    dates, mean = ctx["dates"], ctx["mean"]
    shares = mean / mean.sum(axis=1, keepdims=True)
    regional, national_wd, december, ordinary = _holiday_days(ctx)
    scores = np.array([sunday_likeness(shares[i], templates(shares, dates, i, ordinary))
                       if ordinary[i] else np.nan for i in range(len(dates))])
    limit = float(np.nanmax(scores))
    as_weekday, sd = _weekday_prediction(ctx, model)
    z = (mean.sum(axis=1) * 0.25 - as_weekday) / sd
    rows = []
    for kind, days in (("regional", regional), ("national", national_wd), ("december", december)):
        for d in days:
            i = dates.index(d)
            s = sunday_likeness(shares[i], templates(shares, dates, i, ordinary))
            rows.append({"date": d, "kind": kind, "sunday_likeness": s, "weekday_residual_z": z[i],
                         "observed": bool(s > limit or z[i] >= 2.0),
                         "states_observing": " ".join(c for c in STATES
                                                      if d in state_holidays(c, 2010))})
    for q, v in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1.0)):
        rows.append({"date": None, "kind": f"ordinary_weekday_{q}",
                     "sunday_likeness": float(np.nanquantile(scores, v))})
    table = pd.DataFrame(rows)
    seen = {r["date"] for r in rows if r["kind"] == "regional" and r["observed"]}
    consistent = [c for c in STATES
                  if all((d in state_holidays(c, 2010)) == (d in seen) for d in regional)]
    nat = table[table["kind"] == "national"]
    out = [row("V2 holidays", "nationwide weekday holidays observed", "all",
               float(nat["observed"].mean()), "==1", bool(nat["observed"].all())),
           row("V2 holidays", "holiday-consistent states", " ".join(consistent),
               float(len(consistent)), ">=1", len(consistent) >= 1)]
    return table, out, consistent


def _nearest_sunday(dates, i: int) -> int:
    """Index of the Sunday closest to day i within the year (ties go forward)."""
    ahead, back = 6 - dates[i].weekday(), dates[i].weekday() + 1
    return i + ahead if (ahead <= back and i + ahead < len(dates)) or i - back < 0 else i - back


def holiday_detection_power(ctx: dict, model: al.AggregateLoadModel,
                            fractions=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
                            reps: int = 4, seed: int = 0) -> pd.DataFrame:
    """How large a share of the homes must keep a holiday for `holiday_check` to see it.

    On every ordinary weekday a random share f of the homes is given their own nearest-Sunday
    day instead, and the day is scored with the same rule. Detection near 0 means a holiday
    kept by that share of homes would pass unseen, so "no regional holiday observed" only
    rules out a location where most homes keep it.
    """
    dates, grid = ctx["dates"], ctx["grid"]
    mean = grid.mean(axis=2)
    shares = mean / mean.sum(axis=1, keepdims=True)
    _, _, _, ordinary = _holiday_days(ctx)
    days = np.where(ordinary)[0]
    tmpl = {i: templates(shares, dates, i, ordinary) for i in days}
    limit = max(sunday_likeness(shares[i], tmpl[i]) for i in days)
    as_weekday, sd = _weekday_prediction(ctx, model)
    rng, n_h, rows = np.random.default_rng(seed), grid.shape[2], []
    for f in fractions:
        hits = 0
        for _ in range(reps):
            for i in days:
                mix = grid[i].copy()
                kept = rng.choice(n_h, int(round(f * n_h)), replace=False)
                mix[:, kept] = grid[_nearest_sunday(dates, i)][:, kept]
                day = mix.mean(axis=1)
                z = (day.sum() * 0.25 - as_weekday[i]) / sd
                hits += sunday_likeness(day / day.sum(), tmpl[i]) > limit or z >= 2.0
        rows.append({"share_of_homes_keeping": f, "detection_rate": hits / (reps * len(days)),
                     "trials": reps * len(days)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- V3 daily energy
def _daily_x(dates, national, n_classes: int, k: int, temp, dec_saturday: bool = False,
             weekend_season: bool = True):
    cls = al.day_class(dates, national)
    if dec_saturday:
        for i, d in enumerate(dates):
            if (d.month, d.day) in SPECIAL_DECEMBER and d.weekday() != 6 and d not in national:
                cls[i] = 5
    groups = cls if n_classes == 7 else al.shape_type(cls)
    cols = [np.eye(n_classes)[groups], al.harmonics(dates, k)]
    if weekend_season:
        weekend = al.WEEKEND_CLASSES if n_classes == 7 else (1, 2)
        cols += [(groups == c)[:, None] * al.harmonics(dates, 1) for c in weekend]
    if temp is not None:
        cols.append(np.asarray(temp, dtype=float)[:, None])
    return np.column_stack(cols)


def _daily_specs() -> list[tuple]:
    """(classes, k, temperature, 24/31 Dec as Saturday, site, weekend x season) to compare."""
    p = al.FIT_PROXY
    specs = [(n, k, t, False, p, True) for n in (3, 7) for k in range(5)
             for t in ("none", "normal_anomaly", "anomaly_31d")]
    specs += [(7, 3, "normal_anomaly", False, p, False), (7, 3, "normal_anomaly", True, p, True)]
    return specs + [(7, 3, "normal_anomaly", False, s, True) for s in STATES if s != p]


def daily_model_selection(ctx: dict) -> tuple[pd.DataFrame, list]:
    """Day classes x seasonal harmonics x temperature form x weekend season, blocked CV."""
    dates, keep, nat = ctx["dates"], ctx["keep"], ctx["national"]
    energy = ctx["mean"].sum(axis=1) * 0.25
    t = ctx["temps"][al.FIT_PROXY]
    t31 = (t - t.rolling(31, center=True).mean()).reindex(pd.to_datetime(list(dates))).to_numpy()
    temps = {"none": None, "anomaly_31d": t31}
    b7, b28 = calendar_blocks(len(dates), 7), calendar_blocks(len(dates), 28)
    rows = []
    for n, k, name, dec_sat, site, wknd in _daily_specs():
        temp = ctx["anomalies"][site] if name == "normal_anomaly" else temps[name]
        x = _daily_x(dates, nat, n, k, temp, dec_sat, wknd)
        coefs, *_ = np.linalg.lstsq(x[keep], energy[keep], rcond=None)
        resid = energy[keep] - x[keep] @ coefs
        r2 = 1.0 - resid @ resid / ((energy[keep] - energy[keep].mean()) ** 2).sum()
        rows.append({"classes": n, "k": k, "temperature": name, "weekend_season": wknd,
                     "dec_24_31": "saturday" if dec_sat else "sunday", "site": site,
                     "p": x.shape[1], "n_days": int(keep.sum()), "r2": r2,
                     "cv7": cv_rmse(x, energy, keep, b7), "cv28": cv_rmse(x, energy, keep, b28),
                     "beta": coefs[-1] if temp is not None else np.nan})
    table = pd.DataFrame(rows)
    return table, _selection_rows(table)


def _selection_rows(t: pd.DataFrame) -> list:
    base = t[(t["site"] == al.FIT_PROXY) & (t["dec_24_31"] == "sunday")]

    def cv28(n, k, temp, wknd=True):
        return float(base[(base["classes"] == n) & (base["k"] == k) & (base["temperature"] == temp)
                          & (base["weekend_season"] == wknd)]["cv28"].iloc[0])

    chosen = cv28(7, 3, "normal_anomaly")
    rivals = [("no temperature", 0.98 * cv28(7, 3, "none")),
              ("3 classes", cv28(3, 3, "normal_anomaly")),
              ("31-day anomaly", cv28(7, 3, "anomaly_31d")),
              ("constant weekend uplift", cv28(7, 3, "normal_anomaly", False))]
    return [row("V3 daily", f"cv28 chosen vs {name}", "7 classes K3 weekend x season", chosen,
                f"<={limit:.4f}", chosen <= limit) for name, limit in rivals]


# --------------------------------------------------------------------------- V4 shape
def _bdew_table_shares(shares, dates, types, train, test) -> np.ndarray:
    from ..data.slp import _season

    season = np.array([_season(d) for d in dates])
    out = np.empty((test.sum(), al.N_SLOTS))
    for j, i in enumerate(np.where(test)[0]):
        cell = train & (season == season[i]) & (types == types[i])
        out[j] = shares[cell].mean(axis=0)
    return out


def _harmonic_shares(shares, dates, types, train, test, k: int, temp=None) -> np.ndarray:
    """Fit share coefficients per type on `train`, predict `test`; optional anomaly column."""
    x = np.column_stack([np.ones(len(dates)), al.harmonics(dates, k)]
                        + ([np.asarray(temp, dtype=float)] if temp is not None else []))
    w = np.empty((test.sum(), al.N_SLOTS))
    for c in range(al.N_TYPES):
        g, *_ = np.linalg.lstsq(x[train & (types == c)], shares[train & (types == c)], rcond=None)
        sel = types[test] == c
        w[sel] = x[test][sel] @ g
    return w / w.sum(axis=1, keepdims=True)


def shape_model_selection(ctx: dict) -> tuple[pd.DataFrame, list]:
    """BDEW-style 3 x 3 share table against smooth harmonic shapes, with and without the
    temperature anomaly in the shape, 15-minute RMSE in kW."""
    dates, keep, mean = ctx["dates"], ctx["keep"], ctx["mean"]
    energy = mean.sum(axis=1) * 0.25
    shares = mean / mean.sum(axis=1, keepdims=True)
    types = al.shape_type(al.day_class(dates, ctx["national"]))
    anomaly = ctx["anomalies"][al.FIT_PROXY]
    rows = []
    for variant in ("bdew_table", "harmonic_k1", "harmonic_k2", "harmonic_k3",
                    "harmonic_k2_temperature"):
        res = {"variant": variant}
        for size in (7, 28):
            blocks, err = calendar_blocks(len(dates), size), []
            for b in np.unique(blocks):
                test, train = keep & (blocks == b), keep & (blocks != b)
                if variant == "bdew_table":
                    w = _bdew_table_shares(shares, dates, types, train, test)
                else:
                    temp = anomaly if variant.endswith("temperature") else None
                    w = _harmonic_shares(shares, dates, types, train, test,
                                         int(variant.split("_")[1][1]), temp)
                err.append(energy[test, None] * w / 0.25 - mean[test])
            e = np.concatenate(err)
            res[f"rmse_cv{size}_kw"] = float(np.sqrt(np.mean(e ** 2)))
            if size == 28:
                res["evening_rmse_cv28_kw"] = float(np.sqrt(np.mean(e[:, EVENING] ** 2)))
        rows.append(res)
    table = pd.DataFrame(rows).set_index("variant")
    chosen, ref = table.loc["harmonic_k2", "rmse_cv28_kw"], table.loc["bdew_table", "rmse_cv28_kw"]
    return table.reset_index(), [row("V4 shape", "cv28 15-min RMSE harmonic K2 vs BDEW table",
                                     "harmonic_k2", chosen, f"<={ref:.5f}", chosen <= ref)]


def shape_temperature(ctx: dict) -> pd.DataFrame:
    """Does temperature reshape the day? Per hour: correlation of the share residual (after
    the model's own seasonal shape) with the anomaly, and the share change per K colder."""
    dates, keep, mean = ctx["dates"], ctx["keep"], ctx["mean"]
    shares = mean / mean.sum(axis=1, keepdims=True)
    types = al.shape_type(al.day_class(dates, ctx["national"]))
    fitted = _harmonic_shares(shares, dates, types, keep, keep, al.K_SHAPE)
    hourly = shares[keep].reshape(-1, 24, 4).sum(axis=2)
    resid = hourly - fitted.reshape(-1, 24, 4).sum(axis=2)
    a = ctx["anomalies"][al.FIT_PROXY][keep]
    rows = []
    for h in range(24):
        slope = np.cov(resid[:, h], a)[0, 1] / np.var(a, ddof=1)
        rows.append({"hour_local": h, "corr_with_anomaly": float(np.corrcoef(resid[:, h], a)[0, 1]),
                     "share_change_pct_per_k_colder": float(-100.0 * slope / hourly[:, h].mean())})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- V9 in sample
def _profile_stats(kw: np.ndarray, measured: np.ndarray, dates, keep) -> dict:
    daily, meas_daily = kw.sum(axis=1) * 0.25, measured.sum(axis=1) * 0.25
    resid = (meas_daily - daily)[keep]
    month = np.array([d.month for d in dates])
    winter, summer = np.isin(month, [11, 12, 1, 2]), np.isin(month, [5, 6, 7, 8])
    avg_day = kw.mean(axis=0)
    return {"daily_r2": 1.0 - resid @ resid / ((meas_daily[keep] - meas_daily[keep].mean()) ** 2).sum(),
            "nrmse_15min": float(np.sqrt(np.mean((kw - measured)[keep] ** 2)) / measured[keep].mean()),
            "annual_kwh": float(kw.sum() * 0.25), "peak_to_mean": float(kw.max() / kw.mean()),
            "evening_peak_slot": int(64 + np.argmax(avg_day[64:92])),
            "winter_summer_ratio": float(kw[winter].mean() / kw[summer].mean())}


def insample_2010(ctx: dict, model: al.AggregateLoadModel, h0: np.ndarray) -> tuple[pd.DataFrame, list]:
    """Model, measured 73-household mean and H0 (scaled to the measured energy) on 2010."""
    dates, keep, mean = ctx["dates"], ctx["keep"], ctx["mean"]
    fitted = prediction(model, dates, ctx["national"], ctx["anomalies"][model.proxy])
    h0_scaled = h0 * mean.sum() / h0.sum()
    rows = [{"source": name, **_profile_stats(kw, mean, dates, keep)}
            for name, kw in (("model", fitted), ("measured_73_mean", mean), ("h0", h0_scaled))]
    table = pd.DataFrame(rows).set_index("source")
    fit_e, _ = al.daily_energy(model, dates, ctx["national"], ctx["anomalies"][model.proxy])
    meas_e = mean.sum(axis=1) * 0.25
    gap = abs(fit_e[keep].sum() / meas_e[keep].sum() - 1.0)
    ptm, ptm_meas = table.loc["model", "peak_to_mean"], table.loc["measured_73_mean", "peak_to_mean"]
    return table.reset_index(), [
        row("V9 in-sample", "fit-day energy identity (relative gap)", "model", gap, "<1e-9", gap < 1e-9),
        row("V9 in-sample", "peak-to-mean model <= measured", "model", ptm, f"<={ptm_meas:.3f}",
            ptm <= ptm_meas)]


# --------------------------------------------------------------------------- orchestration
def run_all(ctx: dict, model: al.AggregateLoadModel, out, n_splits: int = 50,
            n_boot: int = 1000, seeds=(0, 1)) -> pd.DataFrame:
    """Run V1-V9, write each table to `out`, return the summary rows (V10 is added later)."""
    from . import aggregate_load_robustness as rob

    summary = []
    table, rows = clock_check(ctx["mean"], ctx["dates"], ctx["national"] | ctx["excluded"])
    table.to_csv(out / "clock_check.csv", index=False)
    summary += rows
    table, rows, consistent = holiday_check(ctx, model)
    table.to_csv(out / "holiday_check.csv", index=False)
    summary += rows
    holiday_detection_power(ctx, model).to_csv(out / "holiday_detection_power.csv", index=False)
    shape_temperature(ctx).to_csv(out / "shape_temperature.csv", index=False)
    for name, fn in (("daily_model_selection", daily_model_selection),
                     ("shape_model_selection", shape_model_selection)):
        table, rows = fn(ctx)
        table.to_csv(out / f"{name}.csv", index=False)
        summary += rows
    h0 = h0_file_grid(ctx["dates"], state_holidays(al.FIT_PROXY, 2010))
    summary += rob.run_robustness(ctx, model, consistent, h0, out, n_splits, n_boot, seeds)
    table, rows = insample_2010(ctx, model, h0)
    table.to_csv(out / "insample_2010.csv", index=False)
    summary += rows
    return pd.DataFrame(summary)

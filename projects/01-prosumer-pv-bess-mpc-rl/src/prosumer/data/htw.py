"""Measured household load: the 74 HTW Berlin profiles (Tjaden et al., 2015).

Dataset: "Representative electrical load profiles of residential buildings in Germany with a
temporal resolution of one second", HTW Berlin, DOI 10.13140/RG.2.1.5112.0080/1. Measured
2010 in the IZES field test "Moderne Energiesparsysteme im Haushalt", 15-minute smart meter
data refined to 1 s. The data is not redistributed; download it from
https://solar.htw-berlin.de/elektrische-lastprofile-fuer-wohngebaeude/ and point
`load_htw_15min` at the folder `CSV_74_Loadprofiles_1min_W_var`.

The files hold active power per phase (PL1, PL2, PL3) in W, one column per household, one
row per minute of 2010, timestamps in CET without daylight saving (MEZ).

Use in this project
  * truth vs forecast: the household's real load is an HTW profile; the controller forecasts
    it with the BDEW standard load profile, as a supplier or an energy management system
    without household-specific history would.
  * calendar mapping: a 2010 profile is replayed on a target year by matching weekday and
    season on the LOCAL clock (people live on daylight saving time even though the meter
    recorded MEZ). Holidays of the target year take a 2010 Sunday.
  * PV screen: the profiles must be gross consumption, otherwise a separately modelled PV
    system would be counted twice. `pv_screen` flags profiles with export or with the midday
    dip on sunny days that a PV household shows.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np
import pandas as pd

from .slp import bavarian_holidays

HTW_TZ = "Etc/GMT-1"                     # MEZ, no daylight saving
FLOOR_KW = 0.060                         # 15-min mean at the synthesis floor (~40 W)
LOCAL_TZ = "Europe/Berlin"


def load_htw_15min(folder: str | Path, cache: str | Path | None = None) -> pd.DataFrame:
    """74 profiles as 15-minute mean total active power in kW, UTC index (2010)."""
    if cache is not None and Path(cache).exists():
        return pd.read_parquet(cache)
    folder = Path(folder)
    total = None
    for phase in ("PL1", "PL2", "PL3"):
        x = pd.read_csv(folder / f"{phase}.csv", header=None, dtype=np.float32).to_numpy()
        total = x if total is None else total + x
    n_min = total.shape[0]
    idx_min = pd.date_range("2010-01-01", periods=n_min, freq="1min", tz=HTW_TZ)
    q = total.reshape(n_min // 15, 15, -1).mean(axis=1) / 1000.0
    idx = idx_min[::15].tz_convert("UTC")
    df = pd.DataFrame(q, index=pd.DatetimeIndex(idx, name="timestamp"),
                      columns=[f"H{i + 1:02d}" for i in range(q.shape[1])])
    min_w = pd.Series(total.min(axis=0), index=df.columns)
    df.attrs["min_1min_w"] = min_w.to_dict()
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache)
        min_w.to_csv(Path(cache).with_suffix(".min_w.csv"))
    return df


def pv_screen(profiles: pd.DataFrame, ghi_daily: pd.Series,
              min_1min_w: pd.Series | None = None) -> pd.DataFrame:
    """Evidence of on-site PV per profile. Returns one row per household.

    negative_minutes : any negative 1-minute value means export, i.e. PV (or a meter
                       measuring net exchange)
    sunny_midday_ratio : summer (May to Aug) midday load 11:00-15:00 CET on the sunniest
                       third of days divided by that on the cloudiest third, each relative
                       to the same day's evening load 18:00-22:00. A gross-consumption
                       household is near 1; net metering behind PV falls clearly below.
    """
    local = profiles.index.tz_convert(HTW_TZ)
    summer = local.month.isin([5, 6, 7, 8])
    day = pd.Index(local.date)
    hour = local.hour
    mid = profiles[summer & (hour >= 11) & (hour < 15)].groupby(day[summer & (hour >= 11)
                                                                     & (hour < 15)]).mean()
    eve = profiles[summer & (hour >= 18) & (hour < 22)].groupby(day[summer & (hour >= 18)
                                                                     & (hour < 22)]).mean()
    rel = mid / eve.replace(0, np.nan)
    g = ghi_daily.reindex(pd.Index(rel.index))
    sunny, cloudy = g >= g.quantile(2 / 3), g <= g.quantile(1 / 3)
    ratio = rel[sunny.to_numpy()].median() / rel[cloudy.to_numpy()].median()
    # The synthesised profiles never go below about 40 W, so export would show up as time
    # pinned at that floor rather than as negative values. Compare the floor share on sunny
    # summer middays with the floor share at night: an absent or frugal household sits on
    # the floor at both, a household with PV behind the meter mainly at sunny middays.
    g_step = pd.Series(local.date).map(ghi_daily).to_numpy(dtype=float)
    sunny_mid = summer & (hour >= 11) & (hour < 15) & (g_step >= np.nanquantile(ghi_daily, 0.8))
    night = (hour >= 1) & (hour < 5)
    floor_mid = (profiles[sunny_mid] < FLOOR_KW).mean()
    floor_night = (profiles[night] < FLOOR_KW).mean()

    out = pd.DataFrame({"sunny_midday_ratio": ratio, "floor_share_sunny_midday": floor_mid,
                        "floor_share_night": floor_night})
    if min_1min_w is not None:
        out["min_1min_w"] = min_1min_w
    out["annual_kwh"] = profiles.sum() * 0.25
    # Either signal suffices. A large PV system pins cloudy middays to the floor as well,
    # which hides it from the sunny/cloudy ratio but not from the midday/night comparison.
    out["pv_suspect"] = (
        (out["floor_share_sunny_midday"] > 1.5 * out["floor_share_night"] + 0.05)
        | (out["sunny_midday_ratio"] < 0.75))
    return out


def household_features(profiles: pd.DataFrame) -> pd.DataFrame:
    """Shape features used to pick a representative household (all scale-free but the first)."""
    local = profiles.index.tz_convert(HTW_TZ)
    total = profiles.sum()
    share = lambda m: profiles[m].sum() / total
    winter = local.month.isin([11, 12, 1, 2])
    summer = local.month.isin([5, 6, 7, 8])
    return pd.DataFrame({
        "annual_kwh": total * 0.25,
        "peak_to_mean": profiles.max() / profiles.mean(),
        "midday_share": share((local.hour >= 11) & (local.hour < 15)),
        "evening_share": share((local.hour >= 18) & (local.hour < 22)),
        "winter_to_summer": profiles[winter].mean() / profiles[summer].mean(),
    })


def representative_household(profiles: pd.DataFrame, exclude=()) -> tuple[str, pd.DataFrame]:
    """The household closest to the median on every feature (robust z-score distance).

    Distances use median and MAD per feature, so one extreme household (for example one
    with an instantaneous electric water heater) does not move the reference.
    """
    f = household_features(profiles.drop(columns=list(exclude)))
    z = (f - f.median()) / (1.4826 * (f - f.median()).abs().median())
    f["distance"] = np.sqrt((z ** 2).sum(axis=1))
    f = f.sort_values("distance")
    return f.index[0], f


def replay_on_calendar(profile_2010: pd.Series, start: str, end: str,
                       annual_kwh: float | None = 3221.0) -> pd.Series:
    """Replay a 2010 profile on local dates [start, end), UTC 15-minute index.

    For each target local date the 2010 source day has the same weekday and the nearest
    day of year (at most 3 days away); target public holidays (Bavaria) take the nearest
    2010 Sunday. Within the day the local clock is matched quarter-hour by quarter-hour.
    The profile is scaled by its 2010 annual energy, so a full target year carries
    approximately `annual_kwh` (None keeps the measured level).
    """
    src_local = profile_2010.index.tz_convert(LOCAL_TZ)
    src = pd.Series(profile_2010.to_numpy(), index=src_local)
    src_slot = src_local.hour * 4 + src_local.minute // 15
    src_days = {}
    for d, g in src.groupby(src_local.date):
        day = pd.Series(g.to_numpy(), index=src_slot[src_local.date == d])
        day = day[~day.index.duplicated(keep="last")]      # spring DST day has 92 slots
        src_days[d] = day.reindex(range(96)).ffill().bfill().to_numpy()

    t0 = pd.Timestamp(start).tz_localize(LOCAL_TZ).tz_convert("UTC")
    t1 = pd.Timestamp(end).tz_localize(LOCAL_TZ).tz_convert("UTC")
    idx = pd.date_range(t0, t1, freq="15min", inclusive="left", name="timestamp")
    local = idx.tz_convert(LOCAL_TZ)
    years = sorted(set(local.year))
    holidays = set().union(*(bavarian_holidays(y) for y in years))

    out = np.empty(len(idx))
    dates = local.date
    slot = (local.hour * 4 + local.minute // 15).to_numpy()
    for d in pd.unique(dates):
        want_wd = 6 if d in holidays else d.weekday()
        doy = min(d.timetuple().tm_yday, 365)
        base = _dt.date(2010, 1, 1) + _dt.timedelta(days=doy - 1)
        cands = [base + _dt.timedelta(days=k) for k in range(-3, 4)]
        cands = [c for c in cands if c.year == 2010 and c.weekday() == want_wd] or \
                [c for c in (base + _dt.timedelta(days=k) for k in range(-7, 8))
                 if c.year == 2010 and c.weekday() == want_wd]
        s = src_days[min(cands, key=lambda c: abs((c - base).days))]
        m = dates == d
        out[m] = s[slot[m]]          # repeated autumn hour replays the same slots

    load = pd.Series(out, index=idx, name="load_kw")
    if annual_kwh is not None:
        full = profile_2010.sum() * 0.25
        load *= annual_kwh / full
    return load

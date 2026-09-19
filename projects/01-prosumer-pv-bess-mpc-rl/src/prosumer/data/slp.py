"""BDEW standard load profile H0 at 15-minute resolution, for any year.

Port of the thesis generator (`00_input_data/Standardized Load Profile/SLP.py` in the thesis
repository), which was fixed to 2024. The representative-day table (96 quarter-hours for
3 seasons x 3 day types) is read from the thesis repository at tag `thesis-v1.0`.

Two choices differ between the thesis and a textbook application of the BDEW method, and both
are parameters rather than silent changes:

  * `holidays`  : BDEW treats public holidays as Sundays and 24/31 December as Saturdays.
                  The thesis profile did not. `holidays=None` reproduces the thesis;
                  `holidays="BY"` applies the Bavarian calendar (the household is in Munich).
  * `local_tz`  : the profile describes behaviour on LOCAL clock time. The thesis used
                  CET without daylight saving (`"Etc/GMT-1"`); `"Europe/Berlin"` follows
                  the legal clock, so on the spring DST day one local hour is skipped and on
                  the autumn DST day one is repeated, as households experience it.
"""
from __future__ import annotations

import datetime as _dt
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from .thesis_data import THESIS_TAG

H0_XLS = ("https://raw.githubusercontent.com/muratkus01/optimization/"
          f"{THESIS_TAG}/00_input_data/Standardized%20Load%20Profile/Haushalt%20Lastprofil.xls")
_COLUMNS = ["winter_saturday", "winter_sunday", "winter_weekday",
            "summer_saturday", "summer_sunday", "summer_weekday",
            "transition_saturday", "transition_sunday", "transition_weekday"]


def fetch_h0_table(cache_dir: str | Path = "data/raw/slp") -> pd.DataFrame:
    """The 96 x 9 representative-day table in W (BDEW H0, 1000 kWh/a basis)."""
    cache_dir = Path(cache_dir)
    csv = cache_dir / "h0_representative_days.csv"
    if csv.exists():
        return pd.read_csv(csv)
    cache_dir.mkdir(parents=True, exist_ok=True)
    xls = cache_dir / "Haushalt Lastprofil.xls"
    with urllib.request.urlopen(H0_XLS, timeout=60) as resp:
        xls.write_bytes(resp.read())
    raw = pd.read_excel(xls, engine="xlrd")
    table = raw.iloc[:96, 1:10].astype(float)
    table.columns = _COLUMNS
    table.to_csv(csv, index=False)
    (cache_dir / "SOURCE.md").write_text(
        f"# Source\n\n- BDEW standard load profile H0 (VDEW representative days)\n"
        f"- File: {H0_XLS}\n")
    return table


# --------------------------------------------------------------------------- calendar
def _easter(year: int) -> _dt.date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return _dt.date(year, month, day + 1)


def bavarian_holidays(year: int) -> set[_dt.date]:
    """Public holidays in Munich (Bavaria, including Assumption Day)."""
    e = _easter(year)
    rel = [-2, 1, 39, 50, 60]          # Good Friday, Easter Monday, Ascension, Whit Monday,
    fixed = [(1, 1), (1, 6), (5, 1),   # Corpus Christi; New Year, Epiphany, Labour Day,
             (8, 15), (10, 3), (11, 1),  # Assumption, Unity Day, All Saints,
             (12, 25), (12, 26)]         # Christmas
    return {e + _dt.timedelta(days=d) for d in rel} | {_dt.date(year, m, d) for m, d in fixed}


def _season(d: _dt.date) -> str:
    if d >= _dt.date(d.year, 11, 1) or d <= _dt.date(d.year, 3, 20):
        return "winter"
    if _dt.date(d.year, 5, 15) <= d <= _dt.date(d.year, 9, 14):
        return "summer"
    return "transition"


def _day_type(d: _dt.date, holidays: set[_dt.date]) -> str:
    if d in holidays or d.weekday() == 6:
        return "sunday"
    if d.weekday() == 5 or (holidays and (d.month, d.day) in {(12, 24), (12, 31)}):
        return "saturday"
    return "weekday"


def _dynamization(day_of_year: np.ndarray) -> np.ndarray:
    t = day_of_year.astype(float)
    return -3.92e-10 * t**4 + 3.2e-7 * t**3 - 7.02e-5 * t**2 + 2.1e-3 * t + 1.24


# --------------------------------------------------------------------------- profile
def h0_profile(start: str, end: str, annual_kwh: float = 3221.0, holidays: str | None = "BY",
               local_tz: str = "Europe/Berlin", table: pd.DataFrame | None = None
               ) -> pd.Series:
    """Household load in kW on a 15-minute UTC grid, `[start, end)` in local dates.

    Scaled so that every full calendar year carries `annual_kwh`, as the thesis did for 2024.
    """
    table = fetch_h0_table() if table is None else table
    t0 = pd.Timestamp(start).tz_localize(local_tz).tz_convert("UTC")
    t1 = pd.Timestamp(end).tz_localize(local_tz).tz_convert("UTC")
    idx = pd.date_range(t0, t1, freq="15min", inclusive="left", name="timestamp")
    local = idx.tz_convert(local_tz)

    dates = local.date
    years = sorted({d.year for d in dates})
    hol = set().union(*(bavarian_holidays(y) for y in years)) if holidays == "BY" else set()
    if holidays not in (None, "BY"):
        raise ValueError("holidays must be None or 'BY'")

    uniq = pd.unique(dates)
    col_of = {d: f"{_season(d)}_{_day_type(d, hol)}" for d in uniq}
    slot = (local.hour * 4 + local.minute // 15).to_numpy()
    cols = np.array([col_of[d] for d in dates])
    values_w = np.empty(len(idx))
    for c in np.unique(cols):
        m = cols == c
        values_w[m] = table[c].to_numpy()[slot[m]]

    kw = values_w / 1000.0 * _dynamization(local.dayofyear.to_numpy())
    load = pd.Series(kw, index=idx, name="load_kw")

    # scale each local calendar year to annual_kwh, using that year's full-year energy
    for y in years:
        full = h0_profile_energy(y, holidays, local_tz, table)
        m = local.year == y
        load[m] *= annual_kwh / full
    return load


def h0_profile_energy(year: int, holidays: str | None, local_tz: str,
                      table: pd.DataFrame) -> float:
    """Unscaled energy (kWh) of one full local calendar year, for normalisation."""
    t0 = pd.Timestamp(f"{year}-01-01").tz_localize(local_tz).tz_convert("UTC")
    t1 = pd.Timestamp(f"{year + 1}-01-01").tz_localize(local_tz).tz_convert("UTC")
    idx = pd.date_range(t0, t1, freq="15min", inclusive="left").tz_convert(local_tz)
    hol = bavarian_holidays(year) if holidays == "BY" else set()
    col_of = {d: f"{_season(d)}_{_day_type(d, hol)}" for d in pd.unique(idx.date)}
    slot = idx.hour * 4 + idx.minute // 15
    w = np.array([table[col_of[d]].to_numpy()[s] for d, s in zip(idx.date, slot)])
    return float(np.sum(w / 1000.0 * _dynamization(idx.dayofyear.to_numpy())) * 0.25)

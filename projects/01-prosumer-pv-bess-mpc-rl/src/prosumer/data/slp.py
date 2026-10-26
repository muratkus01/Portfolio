"""BDEW standard load profile H0 at 15-minute resolution, for any year.

Port of the thesis generator (`00_input_data/Standardized Load Profile/SLP.py` in the thesis
repository), which was fixed to 2024. The representative-day table (96 quarter-hours for
3 seasons x 3 day types) is read from the thesis repository at tag `thesis-v1.0`.

Two choices differ between the thesis and a textbook application of the BDEW method, and both
are parameters rather than silent changes:

  * `holidays`  : BDEW treats public holidays as Sundays and 24/31 December as Saturdays.
                  The thesis profile did not. `holidays=None` reproduces the thesis;
                  `holidays="BY"` applies the Bavarian calendar (the household is in Munich),
                  and any other state code applies that state's calendar.
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


# The 16 federal states, same codes as `mastr` and `national_impact`.
STATES = ("BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV",
          "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH")

# Statutory holidays that only some states keep. Holidays differ by state, so the calendar
# is a channel of state dependence of its own, independent of weather. Only statewide
# holidays are listed: Corpus Christi in parts of Saxony and Thuringia and the Augsburg peace
# festival are local. Assumption Day in Bavaria is local too (Catholic-majority
# municipalities), but it is kept because Munich, the thesis household's city, observes it.
_EPIPHANY = {"BW", "BY", "ST"}                                  # 6 January
_CORPUS_CHRISTI = {"BW", "BY", "HE", "NW", "RP", "SL"}          # Easter + 60
_ASSUMPTION = {"BY", "SL"}                                      # 15 August
_ALL_SAINTS = {"BW", "BY", "NW", "RP", "SL"}                    # 1 November
_REFORMATION = {"BB", "MV", "SN", "ST", "TH"}                   # 31 October
_REFORMATION_SINCE_2018 = {"HB", "HH", "NI", "SH"}
_EASTER_AND_WHIT_SUNDAY = {"BB"}                                # Sundays anyway, listed for completeness


def state_holidays(code: str, year: int) -> set[_dt.date]:
    """Statewide public holidays of one federal state, e.g. `state_holidays("SN", 2025)`.

    Year-dependent rules are applied where a state changed its law during the years this
    project uses: Berlin added Women's Day in 2019 and Mecklenburg-Western Pomerania in 2023,
    Thuringia added World Children's Day in 2019, the four northern states added
    Reformation Day in 2018, and 2017 had it everywhere (500th anniversary). Berlin's one-off
    Liberation Day holidays of 2020 and 2025 are included.
    """
    if code not in STATES:
        raise ValueError(f"unknown state code {code!r}; expected one of {STATES}")
    e = _easter(year)
    days = {e + _dt.timedelta(days=d) for d in (-2, 1, 39, 50)}    # Good Friday, Easter Monday,
    days |= {_dt.date(year, m, d) for m, d in                      # Ascension, Whit Monday
             [(1, 1), (5, 1), (10, 3), (12, 25), (12, 26)]}
    if code in _EPIPHANY:
        days.add(_dt.date(year, 1, 6))
    if code in _CORPUS_CHRISTI:
        days.add(e + _dt.timedelta(days=60))
    if code in _ASSUMPTION:
        days.add(_dt.date(year, 8, 15))
    if code in _ALL_SAINTS:
        days.add(_dt.date(year, 11, 1))
    if (code in _REFORMATION or year == 2017
            or (code in _REFORMATION_SINCE_2018 and year >= 2018)):
        days.add(_dt.date(year, 10, 31))
    if code in _EASTER_AND_WHIT_SUNDAY:
        days |= {e, e + _dt.timedelta(days=49)}
    if (code == "BE" and year >= 2019) or (code == "MV" and year >= 2023):
        days.add(_dt.date(year, 3, 8))                             # Women's Day
    if code == "BE" and year in (2020, 2025):
        days.add(_dt.date(year, 5, 8))                             # Liberation Day, one-off
    if code == "TH" and year >= 2019:
        days.add(_dt.date(year, 9, 20))                            # World Children's Day
    if code == "SN":
        nov22 = _dt.date(year, 11, 22)                             # Day of Repentance: the
        days.add(nov22 - _dt.timedelta(days=(nov22.weekday() - 2) % 7))  # Wednesday before
    return days


def bavarian_holidays(year: int) -> set[_dt.date]:
    """Public holidays in Munich (Bavaria, including Assumption Day)."""
    return state_holidays("BY", year)


def _holiday_set(holidays: str | None, years) -> set[_dt.date]:
    """Holidays of `holidays` (None or a state code) over `years`; validates the code."""
    if holidays is None:
        return set()
    if holidays not in STATES:
        raise ValueError(f"holidays must be None or a state code {STATES}, got {holidays!r}")
    return set().union(*(state_holidays(holidays, y) for y in years))


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
    hol = _holiday_set(holidays, years)

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
    hol = _holiday_set(holidays, [year])
    col_of = {d: f"{_season(d)}_{_day_type(d, hol)}" for d in pd.unique(idx.date)}
    slot = idx.hour * 4 + idx.minute // 15
    w = np.array([table[col_of[d]].to_numpy()[s] for d, s in zip(idx.date, slot)])
    return float(np.sum(w / 1000.0 * _dynamization(idx.dayofyear.to_numpy())) * 0.25)

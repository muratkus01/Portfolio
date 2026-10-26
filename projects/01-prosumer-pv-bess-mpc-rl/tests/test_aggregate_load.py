"""The aggregate household load model: synthetic checks and the committed coefficients.

Everything here runs without the HTW download, the ERA5 cache or the network, so CI
exercises the model. Tests that need the measured data are in test_aggregate_load_data.py.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from prosumer.data import aggregate_load as al
from prosumer.data.slp import STATES
from prosumer.experiments.aggregate_load_fit import fit_calendar_2010, htw_file_grid
from prosumer.experiments.aggregate_load_robustness import moving_block_index, newey_west_se
from prosumer.experiments.aggregate_load_validation import clock_check

DATES_2010 = [dt.date(2010, 1, 1) + dt.timedelta(days=i) for i in range(365)]
TRUE_BETA = -0.08


def _synthetic_shape(rng: np.random.Generator) -> np.ndarray:
    """Share coefficients whose rows sum to exactly 1 for every day, so the fit can be exact."""
    shape = np.zeros((al.N_TYPES, al.N_SLOTS, 1 + 2 * al.K_SHAPE))
    slots = np.arange(al.N_SLOTS)
    for c in range(al.N_TYPES):
        base = 1.0 + 0.8 * np.exp(-((slots - 76 + 4 * c) / 8.0) ** 2)
        shape[c, :, 0] = base / base.sum()
        for j in range(1, shape.shape[2]):
            wiggle = rng.normal(0.0, 1e-4, al.N_SLOTS)
            shape[c, :, j] = wiggle - wiggle.mean()          # sums to zero across the day
    return shape


@pytest.fixture(scope="module")
def synthetic():
    """A known model, the 2010 load it implies, and the fit recovered from that load."""
    rng = np.random.default_rng(7)
    truth = al.AggregateLoadModel(
        alpha=np.array([12.1, 12.0, 12.0, 12.0, 12.7, 13.7, 14.4]),
        energy_harm=np.array([2.0, 0.3, 0.2, -0.1, 0.05, 0.02]),
        weekend_season=np.array([0.4, 0.1, 0.7, -0.2]), beta=TRUE_BETA,
        shape=_synthetic_shape(rng), a_ref=1.0, anomaly_range=(-20.0, 20.0), proxy="NI")
    holidays, _ = fit_calendar_2010()
    anomaly = rng.normal(0.0, 3.0, 365)
    energy, _ = al.daily_energy(truth, DATES_2010, holidays, anomaly)
    mean_kw = energy[:, None] * al.day_shares(truth, DATES_2010, holidays) / 0.25
    fitted = al.fit_model(mean_kw, DATES_2010, holidays, anomaly, np.ones(365, bool))
    return {"truth": truth, "fitted": fitted, "mean_kw": mean_kw, "holidays": holidays,
            "anomaly": anomaly}


def _zero_anomaly(start: str, end: str) -> pd.Series:
    days = pd.date_range(start, end, freq="D", inclusive="left", name="date")
    return pd.Series(0.0, index=days)


# --------------------------------------------------------------------------- calendar terms
def test_harmonics_have_2k_columns_in_cos_sin_order():
    h = al.harmonics([dt.date(2024, 4, 9)], 3)          # day of year 100
    tau = 2 * np.pi * 100 / 365.25
    assert h.shape == (1, 6)
    np.testing.assert_allclose(h[0], [np.cos(tau), np.sin(tau), np.cos(2 * tau),
                                      np.sin(2 * tau), np.cos(3 * tau), np.sin(3 * tau)])
    assert al.harmonics([dt.date(2024, 1, 1)], 0).shape == (1, 0)


def test_day_class_uses_state_holidays_and_new_years_eve():
    corpus_christi, reformation = dt.date(2024, 5, 30), dt.date(2024, 10, 31)   # Thursdays
    by = al.state_holidays("BY", 2024)
    assert al.day_class([corpus_christi], by)[0] == 6
    assert al.day_class([corpus_christi], al.state_holidays("BE", 2024))[0] == 3
    assert al.day_class([reformation], al.state_holidays("SN", 2024))[0] == 6
    assert al.day_class([reformation], by)[0] == 3
    for state in ("BY", "BE", "NI"):
        hol = al.state_holidays(state, 2024)
        assert list(al.day_class([dt.date(2024, 12, 24), dt.date(2024, 12, 31)], hol)) == [6, 6]
    np.testing.assert_array_equal(al.shape_type(np.arange(7)), [0, 0, 0, 0, 0, 1, 2])


# --------------------------------------------------------------------------- temperature
@pytest.mark.parametrize("noise_k, tolerance_k", [(0.0, 1e-9), (1.0, 0.1)])
def test_fit_normal_recovers_synthetic_climatology(noise_k, tolerance_k):
    """Exact without noise; with 1 K daily noise over 30 years the normal is within 0.1 K."""
    days = pd.date_range("1991-01-01", "2021-01-01", freq="D", inclusive="left")
    tau = 2 * np.pi * days.day_of_year.to_numpy() / 365.25
    clim = 9.5 - 8.5 * np.cos(tau - 0.35) + 0.6 * np.cos(2 * tau)
    noise = np.random.default_rng(1).normal(0.0, noise_k, len(days))
    coefs = al.fit_normal(pd.Series(clim + noise, index=days))
    check = pd.date_range("2005-01-01", "2006-01-01", freq="D", inclusive="left")
    anomaly = al.temperature_anomaly(pd.Series(clim[days.isin(check)], index=check), coefs)
    assert np.abs(anomaly).max() < tolerance_k


def test_fit_normal_rejects_a_short_record():
    days = pd.date_range("2015-01-01", "2020-12-31", freq="D")
    with pytest.raises(ValueError):
        al.fit_normal(pd.Series(10.0, index=days))


def test_daily_fit_recovers_slope_despite_seasonal_confound():
    """Winter is cold AND dark: a raw-temperature slope without seasonal terms absorbs the
    daylight cycle; the anomaly with three harmonics recovers the true slope."""
    holidays, _ = fit_calendar_2010()
    tau = 2 * np.pi * np.arange(1, 366) / 365.25
    anomaly = np.random.default_rng(3).normal(0.0, 3.0, 365)
    temp = 10.0 - 9.0 * np.cos(tau) + anomaly
    classes = al.day_class(DATES_2010, holidays)
    energy = 12.0 + 0.8 * (classes == 6) + 3.0 * np.cos(tau) + TRUE_BETA * anomaly
    keep = np.ones(365, bool)
    good, *_ = np.linalg.lstsq(al.energy_design(DATES_2010, holidays, anomaly)[keep], energy,
                               rcond=None)
    naive, *_ = np.linalg.lstsq(al.energy_design(DATES_2010, holidays, temp, k=0), energy,
                                rcond=None)
    assert good[-1] == pytest.approx(TRUE_BETA, rel=0.10)
    assert abs(naive[-1]) > 2 * abs(TRUE_BETA)


# --------------------------------------------------------------------------- fit and predict
def test_fit_then_predict_reproduces_a_known_synthetic_profile(synthetic):
    f, t = synthetic["fitted"], synthetic["truth"]
    np.testing.assert_allclose(f.alpha, t.alpha, atol=1e-9)
    np.testing.assert_allclose(f.energy_harm, t.energy_harm, atol=1e-9)
    np.testing.assert_allclose(f.weekend_season, t.weekend_season, atol=1e-9)
    assert f.beta == pytest.approx(TRUE_BETA, abs=1e-9)
    energy, _ = al.daily_energy(f, DATES_2010, synthetic["holidays"], synthetic["anomaly"])
    predicted = energy[:, None] * al.day_shares(f, DATES_2010, synthetic["holidays"]) / 0.25
    np.testing.assert_allclose(predicted, synthetic["mean_kw"], rtol=1e-9)


def test_fit_year_energy_is_the_reference_at_zero_anomaly(synthetic):
    f = synthetic["fitted"]
    energy, _ = al.daily_energy(f, DATES_2010, synthetic["holidays"], np.zeros(365))
    assert energy.sum() == pytest.approx(f.a_ref, rel=1e-12)


def test_colder_day_draws_more_energy_with_unchanged_shape(synthetic):
    f, hol = synthetic["fitted"], synthetic["holidays"]
    day = [dt.date(2010, 2, 10)]
    warm, _ = al.daily_energy(f, day, hol, np.array([0.0]))
    cold, _ = al.daily_energy(f, day, hol, np.array([-5.0]))
    assert cold[0] - warm[0] == pytest.approx(5 * abs(f.beta), abs=1e-12)
    a = _zero_anomaly("2024-02-14", "2024-02-15")
    ratio = (al.unit_load(f, "NI", "2024-02-14", "2024-02-15", a - 5.0)
             / al.unit_load(f, "NI", "2024-02-14", "2024-02-15", a))
    assert ratio.iloc[0] > 1.0
    np.testing.assert_allclose(ratio, ratio.iloc[0])        # every quarter-hour scales alike


def test_anomaly_is_clipped_to_fit_range_and_counted(synthetic):
    f, hol = synthetic["fitted"], synthetic["holidays"]
    days = [dt.date(2010, 7, 1), dt.date(2010, 7, 2), dt.date(2010, 7, 3)]
    lo, hi = f.anomaly_range
    energy, clipped = al.daily_energy(f, days, hol, np.array([hi + 7.0, 0.0, lo - 3.0]))
    at_edges, none = al.daily_energy(f, days, hol, np.array([hi, 0.0, lo]))
    assert clipped == 2 and none == 0
    np.testing.assert_allclose(energy, at_edges)


def test_shares_sum_to_one_and_stay_positive(synthetic):
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(366)]
    w = al.day_shares(synthetic["fitted"], days, al.state_holidays("BY", 2024))
    np.testing.assert_allclose(w.sum(axis=1), 1.0)
    assert (w > 0).all()


# --------------------------------------------------------------------------- time handling
def test_household_load_follows_local_clock_across_dst(synthetic):
    f = synthetic["fitted"]
    p = al.household_load(f, "BY", "2025-03-29", "2025-10-28", annual_kwh=1000.0,
                          anomaly=_zero_anomaly("2025-03-29", "2025-10-28"))
    assert ((p.index[1:] - p.index[:-1]) == pd.Timedelta("15min")).all()
    local = p.index.tz_convert("Europe/Berlin")
    per_day = pd.Series(1, index=local.date).groupby(level=0).sum()
    assert per_day[dt.date(2025, 3, 30)] == 92 and per_day[dt.date(2025, 10, 26)] == 100
    hol = al.state_holidays("BY", 2025)
    for day in (dt.date(2025, 3, 29), dt.date(2025, 3, 30), dt.date(2025, 10, 26),
                dt.date(2025, 10, 27)):
        energy, _ = al.daily_energy(f, [day], hol, np.zeros(1))
        share = al.day_shares(f, [day], hol)[0, 32]                 # 08:00 local
        at_eight = p[(local.date == day) & (local.hour == 8) & (local.minute == 0)]
        assert at_eight.iloc[0] == pytest.approx(1000.0 * energy[0] * share / (0.25 * f.a_ref))


def test_unit_load_energy_equals_daily_model_on_normal_days(synthetic):
    f = synthetic["fitted"]
    u = al.unit_load(f, "NI", "2024-06-03", "2024-06-10", _zero_anomaly("2024-06-03", "2024-06-10"))
    daily = u.groupby(u.index.tz_convert("Europe/Berlin").date).sum() * 0.25 * f.a_ref
    energy, _ = al.daily_energy(f, list(daily.index), al.state_holidays("NI", 2024), np.zeros(7))
    np.testing.assert_allclose(daily.to_numpy(), energy)


def test_missing_temperature_day_raises(synthetic):
    a = _zero_anomaly("2024-06-03", "2024-06-10").drop(pd.Timestamp("2024-06-05"))
    with pytest.raises(ValueError, match="no temperature anomaly"):
        al.unit_load(synthetic["fitted"], "NI", "2024-06-03", "2024-06-10", a)


def test_annual_kwh_is_required(synthetic):
    with pytest.raises(TypeError):
        al.household_load(synthetic["fitted"], "NI", "2024-06-03", "2024-06-04",
                          anomaly=_zero_anomaly("2024-06-03", "2024-06-04"))


def test_fleet_load_scales_to_megawatt_and_forward_fills_monthly_counts():
    idx = pd.date_range("2025-01-01", "2025-03-01", freq="15min", tz="UTC", inclusive="left")
    one_kw = pd.Series(1.0, index=idx)
    counts = pd.Series([1000.0, 2000.0], index=pd.to_datetime(["2025-01-01", "2025-02-01"], utc=True))
    fleet = al.fleet_load(one_kw, counts)
    feb = idx >= pd.Timestamp("2025-02-01", tz="UTC")
    assert fleet[~feb].eq(1.0).all() and fleet[feb].eq(2.0).all()
    assert al.fleet_load(one_kw, 500).eq(0.5).all()
    with pytest.raises(ValueError):
        al.fleet_load(one_kw, counts.iloc[1:])


def test_fleet_load_reads_register_dates_as_local_midnight(synthetic):
    """mastr.count_by_state() has tz-naive monthly dates; household load is on UTC and starts
    at 23:00 UTC the day before. The step must land at local midnight, not an hour later."""
    p = al.household_load(synthetic["fitted"], "BY", "2025-01-01", "2025-03-01", annual_kwh=1000.0,
                          anomaly=_zero_anomaly("2025-01-01", "2025-03-01"))
    counts = pd.Series([1000.0, 2000.0], index=pd.to_datetime(["2025-01-01", "2025-02-01"]))
    n = al.fleet_load(p, counts) / p * 1000.0
    local = p.index.tz_convert("Europe/Berlin")
    feb = local >= pd.Timestamp("2025-02-01", tz="Europe/Berlin")
    np.testing.assert_allclose(n[~feb], 1000.0)
    np.testing.assert_allclose(n[feb], 2000.0)


def test_energy_design_carries_a_seasonal_weekend_uplift():
    """Saturday and Sunday-type days get their own first harmonic; weekdays do not."""
    days = [dt.date(2024, 1, 8) + dt.timedelta(days=i) for i in range(7)]       # Mon..Sun
    x = al.energy_design(days, set(), np.zeros(7))
    assert x.shape == (7, 7 + 2 * al.K_ENERGY + 4 + 1)
    weekend = x[:, 7 + 2 * al.K_ENERGY:-1]
    assert (weekend[:5] == 0).all()
    np.testing.assert_allclose(weekend[5, :2], al.harmonics([days[5]], 1)[0])
    np.testing.assert_allclose(weekend[6, 2:], al.harmonics([days[6]], 1)[0])
    as_weekday = al.energy_design(days, set(), np.zeros(7), classes=np.full(7, 2))
    assert (as_weekday[:, 7 + 2 * al.K_ENERGY:-1] == 0).all()


def test_model_save_load_round_trip_is_lossless(synthetic, tmp_path):
    f = synthetic["fitted"]
    al.save_model(f, tmp_path)
    back = al.load_model(tmp_path)
    np.testing.assert_array_equal(back.alpha, f.alpha)
    np.testing.assert_array_equal(back.energy_harm, f.energy_harm)
    np.testing.assert_array_equal(back.weekend_season, f.weekend_season)
    np.testing.assert_array_equal(back.shape, f.shape)
    assert (back.beta, back.a_ref, back.anomaly_range, back.proxy) == (
        f.beta, f.a_ref, f.anomaly_range, f.proxy)


def test_load_model_rejects_a_file_without_the_weekend_block(synthetic, tmp_path):
    """A coefficients.csv written before the weekend terms existed must fail clearly."""
    al.save_model(synthetic["fitted"], tmp_path)
    coef = pd.read_csv(tmp_path / "coefficients.csv")
    coef[~coef["term"].str.startswith("weekend_")].to_csv(tmp_path / "coefficients.csv", index=False)
    with pytest.raises(ValueError, match="malformed"):
        al.load_model(tmp_path)


# --------------------------------------------------------------------------- fit helpers
def test_fit_calendar_2010_uses_359_days_and_9_national_holidays():
    national, excluded = fit_calendar_2010()
    assert len(national) == 9
    assert 365 - len(excluded) == 359
    assert {dt.date(2010, 1, 6), dt.date(2010, 6, 3), dt.date(2010, 11, 1), dt.date(2010, 11, 17),
            dt.date(2010, 3, 28), dt.date(2010, 10, 31)} == excluded


def test_htw_file_grid_maps_row_to_label_date_and_slot():
    labels = pd.date_range("2010-01-01", periods=365 * 96, freq="15min", tz="Etc/GMT-1")
    slot = np.tile(np.arange(96, dtype=float), 365)
    frame = pd.DataFrame({"H01": slot, "H02": slot, "H49": -slot}, index=labels.tz_convert("UTC"))
    dates, grid = htw_file_grid(frame, exclude=["H49"])
    assert dates[0] == dt.date(2010, 1, 1) and dates[-1] == dt.date(2010, 12, 31)
    assert grid.shape == (365, 96, 2)
    np.testing.assert_array_equal(grid[200, :, 1], np.arange(96))


def _ramp_year(summer_shift_slots: int) -> np.ndarray:
    """Night 0.2 kW, a step to 1 kW at 06:00, moved by `summer_shift_slots` in summer time."""
    from prosumer.experiments.aggregate_load_validation import _summer_time

    grid = np.full((365, 96), 0.2)
    for i, d in enumerate(DATES_2010):
        start = 24 + (summer_shift_slots if _summer_time(d) else 0)
        grid[i, start:44] = 1.0
    return grid


def test_clock_check_detects_an_injected_one_hour_shift():
    table, _ = clock_check(_ramp_year(0), DATES_2010, set())
    legal = table[table["reading"] == "file_clock"]["step_h"]
    np.testing.assert_allclose(legal, 0.0, atol=0.1)
    table, _ = clock_check(_ramp_year(-4), DATES_2010, set())      # what MEZ labels would show
    shifted = table[table["reading"] == "file_clock"].set_index("transition")["step_h"]
    assert shifted["spring"] == pytest.approx(-1.0, abs=0.1)
    assert shifted["autumn"] == pytest.approx(1.0, abs=0.1)


def test_newey_west_equals_naive_on_white_noise_and_exceeds_it_on_ar1():
    rng = np.random.default_rng(11)
    n = 4000
    x = np.column_stack([np.ones(n), rng.normal(size=n)])
    white = rng.normal(size=n)
    naive = np.sqrt(np.diag(np.linalg.inv(x.T @ x)) * white.var())
    assert newey_west_se(x, white, 7)[1] == pytest.approx(naive[1], rel=0.15)
    ar_x, ar_u = np.zeros(n), np.zeros(n)
    for t in range(1, n):
        ar_x[t] = 0.8 * ar_x[t - 1] + rng.normal()
        ar_u[t] = 0.8 * ar_u[t - 1] + rng.normal()
    xa = np.column_stack([np.ones(n), ar_x])
    naive_ar = np.sqrt(np.diag(np.linalg.inv(xa.T @ xa)) * ar_u.var())
    assert newey_west_se(xa, ar_u, 14)[1] > 1.5 * naive_ar[1]


def test_moving_block_index_has_length_n_and_valid_range():
    idx = moving_block_index(359, 14, np.random.default_rng(0))
    assert len(idx) == 359 and idx.min() >= 0 and idx.max() <= 358
    assert (np.diff(idx[:14]) == 1).all()


# --------------------------------------------------------------------------- committed model
def test_committed_model_is_well_formed():
    m = al.load_model()
    assert m.alpha.shape == (7,) and m.energy_harm.shape == (6,) and m.shape.shape == (3, 96, 5)
    assert m.weekend_season.shape == (4,)
    assert m.beta < 0 and m.a_ref > 0 and m.anomaly_range[0] < 0 < m.anomaly_range[1]
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(366)]
    x = np.column_stack([np.ones(366), al.harmonics(days, al.K_SHAPE)])
    for c in range(al.N_TYPES):
        w = x @ m.shape[c].T
        assert (w > 0).all()
        np.testing.assert_allclose(w.sum(axis=1), 1.0, atol=1e-5)
    normals = al.load_state_normals()
    assert sorted(normals) == sorted(STATES)
    assert all(v.shape == (5,) for v in normals.values())


def test_committed_validation_passes():
    summary = pd.read_csv(al.MODEL_DIR / "validation_summary.csv")
    checks = {c.split()[0] for c in summary["check"]}
    assert checks == {f"V{i}" for i in (1, 2, 3, 4, 5, 6, 7, 9, 10)}
    failed = summary[~summary["passed"].astype(bool)]
    assert failed.empty, failed.to_string()


def test_household_load_runs_from_committed_model_with_injected_anomaly():
    m = al.load_model()
    p = al.household_load(m, "BY", "2025-01-13", "2025-01-20", annual_kwh=m.a_ref,
                          anomaly=_zero_anomaly("2025-01-13", "2025-01-20"))
    assert len(p) == 7 * 96 and (p > 0).all()
    assert 0.5 < p.attrs["realised_kwh"] / (m.a_ref * 7 / 365) < 1.5


def test_committed_fit_year_energy_identity():
    m = al.load_model()
    national, _ = fit_calendar_2010()
    energy, _ = al.daily_energy(m, DATES_2010, national, np.zeros(365))
    assert energy.sum() == pytest.approx(m.a_ref, rel=1e-9)

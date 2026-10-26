"""The MaStR fleet loader, and the invariants that keep the national analysis honest.

These tests exist because the three numbers they check were wrong before: the national
analysis carried a hand-written extrapolation for 2025 and 2026 that overstated the fleet by
24 % and 14 %, and the README described the evaluated asset as 9.37 kWh / 5.63 kW while every
result was produced with the `SiteConfig()` defaults of 8.9 kWh / 5.51 kW.

They skip rather than fail when the download is absent, because the CSVs are gitignored.
"""
from __future__ import annotations

import pandas as pd
import pytest

from prosumer.config import SiteConfig
from prosumer.data import mastr

pytestmark = pytest.mark.skipif(
    not (mastr.RAW / "home_storage_mean_energy_power.csv").exists(),
    reason="MaStR download absent; see data/raw/mastr/SOURCE.md",
)


# --------------------------------------------------------------------------- unit parsing
@pytest.mark.parametrize("text,expected", [
    ("932 kWh", 932.0),
    ("1.29 MWh", 1_290.0),
    ("5.38 GWh", 5_380_000.0),
    ("6.09 kW", 6.09),
    ("390 MW", 390_000.0),
    ("2253070", 2_253_070.0),
])
def test_parse_quantity_normalises_to_kilo(text, expected):
    assert mastr.parse_quantity(text) == pytest.approx(expected)


def test_parse_quantity_rejects_unknown_unit():
    """A silent fallback here would turn a GWh into a kWh, a factor of a million."""
    with pytest.raises(ValueError):
        mastr.parse_quantity("5.38 furlongs")


# --------------------------------------------------------------------------- coverage
def test_coverage_reports_the_partial_final_month():
    cov = mastr.coverage()
    assert cov["first"] == "2000-01-01"
    assert cov["last"] == "2026-01-13"
    assert cov["last_row_is_partial_month"] is True
    # Anything labelled "2026" rests on a single full month. If a later export lands, this
    # test is the reminder to restate every 2026 figure rather than silently keep the label.
    assert cov["full_months_in_2026"] == 1


# --------------------------------------------------------------------------- published means
@pytest.mark.parametrize("date,kwh,kw", [
    ("2024-01-01", 8.96, 5.53),
    ("2025-01-01", 8.92, 5.66),
    ("2026-01-01", 8.73, 5.55),
])
def test_published_home_storage_mean(date, kwh, kw):
    snap = mastr.snapshot(date)
    assert snap["mean_kwh"] == pytest.approx(kwh, abs=0.005)
    assert snap["mean_kw"] == pytest.approx(kw, abs=0.005)


def test_mean_size_falls_after_2024():
    """Newly registered home storages are smaller than the installed base.

    The previous extrapolation assumed the opposite and raised the mean year on year. A sign
    error in a trend is invisible in a single number and obvious in a test.
    """
    mean = mastr.home_storage_mean()["mean_kwh"]
    y = {d: float(mean[f"{d}-01-01"]) for d in (2024, 2025, 2026)}
    assert y[2024] > y[2025] > y[2026]


def test_snapshot_rejects_a_date_not_in_the_series():
    with pytest.raises(KeyError):
        mastr.snapshot("2026-06-01")


# --------------------------------------------------------------------------- per-state counts
def test_every_state_has_a_count_file():
    counts = mastr.count_by_state("Home storage")
    assert len(counts.columns) == 17  # 16 states plus the DE sum
    assert set(mastr.STATE_CODE.values()) <= set(counts.columns)


def test_state_counts_sum_to_the_de_column():
    counts = mastr.count_by_state("Home storage")
    row = counts.loc[pd.Timestamp("2024-01-01")]
    states = row.drop(labels=["DE"])
    assert float(row["DE"]) == pytest.approx(float(states.sum()))


def test_lithium_ion_is_most_but_not_all_of_the_home_fleet():
    """The per-state files are lithium-ion; the Germany-wide segment file is all chemistries.

    Conflating the two understates the fleet by about 3 %, which is small enough to go
    unnoticed and large enough to matter in a national aggregate.
    """
    li = float(mastr.count_by_state("Home storage").loc[pd.Timestamp("2024-01-01"), "DE"])
    allchem = mastr.snapshot("2024-01-01")["home_storage_count"]
    share = li / allchem
    assert 0.95 < share < 0.99


# --------------------------------------------------------------------------- national analysis
def test_national_totals_reproduce_the_thesis_2024_table():
    """Gate: the 2024 snapshot must still match the figures published in the thesis."""
    from prosumer.experiments.national_impact import get_national_totals
    t = get_national_totals(2024)
    assert t["total_bess_units"] == pytest.approx(1_117_000, rel=0.01)
    assert t["total_capacity_gwh"] == pytest.approx(9.97, rel=0.01)
    assert t["total_power_gw"] == pytest.approx(6.17, rel=0.01)


def test_national_totals_come_from_the_register_not_an_extrapolation():
    from prosumer.experiments.national_impact import FEDERAL_STATES, SNAPSHOTS
    counts = mastr.count_by_state("Home storage")
    for year, date in SNAPSHOTS.items():
        ts = pd.Timestamp(date)
        for code, state in FEDERAL_STATES.items():
            assert getattr(state, f"bess_count_{year}") == int(round(float(counts.loc[ts, code])))


def test_fleet_counts_are_not_round_numbers():
    """The 2025 and 2026 figures used to be hand-written round numbers, all 16 of them.

    Register counts are never round, so this catches a silent return to estimates.
    """
    from prosumer.experiments.national_impact import FEDERAL_STATES
    for year in (2024, 2025, 2026):
        counts = [getattr(s, f"bess_count_{year}") for s in FEDERAL_STATES.values()]
        assert sum(c % 500 == 0 for c in counts) <= 1


# --------------------------------------------------------------------------- the site config
def test_site_config_matches_the_published_german_mean():
    """`SiteConfig()` is the mean German home storage of January 2024, within rounding.

    The README claimed 9.37 kWh / 5.63 kW for the same runs. That figure is the per-state
    quotient for Bavaria, which is biased because per-state energy includes large-scale
    batteries while the count is home only. This test pins the headline asset to the one
    unbiased published number.
    """
    snap = mastr.snapshot("2024-01-01")
    cfg = SiteConfig()
    assert cfg.e_bess == pytest.approx(snap["mean_kwh"], abs=0.1)
    assert cfg.p_inv == pytest.approx(snap["mean_kw"], abs=0.1)


def test_per_state_mean_size_is_biased_high_and_says_so():
    """The derived per-state size must stay visibly worse than the published mean."""
    est = mastr.mean_size_by_state("2024-01-01")
    published = mastr.snapshot("2024-01-01")["mean_kwh"]
    assert est["mean_kwh_uncorrected"].max() > 1.4 * published
    assert 0.80 < est.attrs["home_energy_share_de"] < 0.85
    assert (est["home_count_share_of_all"] > 0.98).all()

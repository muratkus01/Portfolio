"""Gate 0: the package reproduces the PUBLISHED thesis, full year 2024.

Reference: the 'Yearly Summary' of the three scenarios in
https://github.com/muratkus01/optimization at tag thesis-v1.0, copied into
tests/reference/thesis_2024_yearly_summary.csv. The input year is fetched from the same tag
on first use (network needed once; the test skips when offline).

The scenarios run on this package's own controllers and settlement, with the thesis
configuration from `prosumer.thesis`. No thesis code is imported.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from prosumer.baselines.ladder import b1_rule_based, b2_perfect_foresight
from prosumer.market import tariff
from prosumer.model import site
from prosumer.reporting.thesis_kpis import thesis_summary
from prosumer.thesis import (COSTS_OPTIMIZED, COSTS_PV_ONLY, COSTS_RULE_BASED,
                             THESIS_MILP_RUN, THESIS_RETAIL_TARIFF, THESIS_RULE_RUN)

REFERENCE = Path(__file__).parent / "reference" / "thesis_2024_yearly_summary.csv"
CACHE = Path(__file__).parents[1] / "data" / "raw" / "thesis"


@pytest.fixture(scope="module")
def year():
    from prosumer.data.thesis_data import fetch_thesis_year, load_thesis_year
    try:
        return load_thesis_year(fetch_thesis_year(CACHE))
    except OSError as exc:                                          # pragma: no cover
        pytest.skip(f"thesis input year not available offline: {exc}")


@pytest.fixture(scope="module")
def reference():
    return pd.read_csv(REFERENCE, encoding="utf-8").set_index("scenario")


def _summary(res, df, run, cost, basis, has_battery=True):
    return thesis_summary(res, df["load_kw"], df["pv_kw"], df["ep_buy"], df["ep_sell"],
                          df.index, run.dt, run.site, cost, basis, has_battery=has_battery)


def _assert_matches(ours: dict, ref: pd.Series):
    for col, expected in ref.items():
        assert ours[col] == pytest.approx(expected, abs=0.011, rel=1e-4), col


def test_time_convention_and_tariff_are_exact(year):
    """Retail prices rebuilt from EPEX match the thesis columns; the UTC index is regular."""
    spot = year["spot_eur_per_kwh"].to_numpy()
    np.testing.assert_allclose(tariff.import_price(spot, year.index, THESIS_RETAIL_TARIFF),
                               year["ep_buy"], atol=1e-6)
    np.testing.assert_allclose(tariff.export_price(spot, THESIS_RETAIL_TARIFF),
                               year["ep_sell"], atol=1e-6)
    assert len(year) == 8784 and (year.index.to_series().diff().dropna()
                                  == pd.Timedelta("1h")).all()


def test_pv_only(year, reference):
    run = THESIS_RULE_RUN
    res = site.simulate(np.zeros(len(year)), year["load_kw"].to_numpy(),
                        year["pv_kw"].to_numpy(), run.dt, run.site)
    _assert_matches(_summary(res, year, run, COSTS_PV_ONLY, "dc_to_load", has_battery=False),
                    reference.loc["just_pv"])


def test_rule_based(year, reference):
    run = THESIS_RULE_RUN
    res = b1_rule_based(year["load_kw"].to_numpy(), year["pv_kw"].to_numpy(), run.dt, run)
    _assert_matches(_summary(res, year, run, COSTS_RULE_BASED, "dc_to_load"),
                    reference.loc["rule_based_pv_bess"])


def test_perfect_foresight_milp(year, reference):
    run = THESIS_MILP_RUN
    spot = year["spot_eur_per_kwh"].to_numpy()
    pi = tariff.import_price(spot, year.index, run.tariff)
    pe = tariff.export_price(spot, run.tariff)
    res = b2_perfect_foresight(year["load_kw"].to_numpy(), year["pv_kw"].to_numpy(),
                               pi, pe, run.dt, run)
    assert res is not None
    _assert_matches(_summary(res, year, run, COSTS_OPTIMIZED, "load_met"),
                    reference.loc["optimized_pv_bess"])


def test_thesis_pv_is_one_hour_early_and_the_correction_fixes_it():
    """Published PV peaks before solar noon; the corrected series is centred on it."""
    from prosumer.data.thesis_data import fetch_thesis_year, load_thesis_year

    def centroid_cet(pv):
        loc = pv.index.tz_convert("Etc/GMT-1")
        h = loc.hour + 0.5
        return float(((pv * h).groupby(loc.date).sum() / pv.groupby(loc.date).sum()).mean())

    path = fetch_thesis_year(CACHE)
    published = load_thesis_year(path)["pv_kw"]
    corrected = load_thesis_year(path, correct_pv_timing=True)["pv_kw"]
    assert centroid_cet(published) < 11.5                   # solar noon in Munich ~12:15 CET
    assert 11.9 < centroid_cet(corrected) < 12.5
    assert corrected.sum() == pytest.approx(published.sum(), rel=1e-4)

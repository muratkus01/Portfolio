"""Tests for Germany-wide system-level impact analysis and merit-order displacement.

The fleet tests read the gitignored MaStR download and skip without it. The merit-order
tests use no data and always run. The stress-day price test reads the gitignored Energy-Charts
price cache that `prosumer build-data` writes, and skips without it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from prosumer.data import mastr
from prosumer.experiments.national_impact import (
    MARGINAL_COST_BESS_EUR_MWH,
    REPRESENTATIVE_DAYS,
    compute_grid_exchange_shift,
    evaluate_fossil_gas_displacement,
    federal_states,
    get_national_totals,
)

needs_mastr = pytest.mark.skipif(
    not mastr.available(), reason="MaStR download absent; see data/raw/mastr/SOURCE.md"
)
PRICE_CACHE = (Path(__file__).parents[1] / "data" / "raw" / "prices"
               / "energycharts_price_DE-LU_2024-01-01_2026-09-18.json")


@needs_mastr
def test_federal_states_completeness():
    """Verify all 16 German federal states are configured with coordinates and fleet data."""
    states = federal_states()
    assert len(states) == 16
    for code, state in states.items():
        assert state.latitude > 45.0
        assert state.longitude > 5.0
        assert state.bess_count_2024 > 0
        assert state.avg_capacity_kwh_2024 > 5.0
        assert state.avg_power_kw_2024 > 3.0


@needs_mastr
def test_national_totals_2024_matches_thesis():
    """The 2024 snapshot still reproduces Master's Thesis Table 10-11.

    Exact equality was asserted until 2026-10-26, when the fleet table stopped being a
    hardcoded copy of that table and started being read from the MaStR register
    (`prosumer.data.mastr`). The register snapshot of 1 January 2024 gives 1,119,240 units
    against the thesis table's 1,116,967, a difference of 0.20 % that comes from the export
    date, so the tolerance is a fifth of a percent rather than zero.
    """
    totals = get_national_totals(2024)
    # Thesis Table 10-11: 1,116,967 units, 9,972.33 MWh (9.972 GWh), 6,167 MW (6.167 GW)
    assert totals["total_bess_units"] == pytest.approx(1_116_967, rel=0.005)
    assert totals["total_capacity_gwh"] == pytest.approx(9.972, rel=0.01)
    assert totals["total_power_gw"] == pytest.approx(6.167, rel=0.01)
    # Mean size is now the published Germany-wide mean, identical for every state, so the
    # weighted and unweighted means coincide at the January 2024 value of 8.96 kWh / 5.53 kW.
    assert totals["mean_capacity_kwh"] == pytest.approx(8.96, abs=0.02)
    assert totals["mean_power_kw"] == pytest.approx(5.53, abs=0.02)


@needs_mastr
def test_national_totals_2025_and_2026_expansion():
    """2025 and 2026 come from the register, not from a growth assumption.

    The previous thresholds (>2.0 M units in 2025, >=2.4 M in 2026) were written against a
    hand-made extrapolation that overstated the fleet by 24 % and 14 %. The register gives
    1,670,776 units at 2025-01-01 and 2,190,625 at 2026-01-01. Bounds are two-sided on
    purpose: a one-sided ">" cannot catch an overstatement, which is how the old numbers
    survived.
    """
    t2024 = get_national_totals(2024)
    t2025 = get_national_totals(2025)
    t2026 = get_national_totals(2026)

    assert t2025["total_bess_units"] == pytest.approx(1_670_776, rel=0.001)
    assert t2025["total_capacity_gwh"] == pytest.approx(14.90, rel=0.01)
    assert t2025["total_power_gw"] == pytest.approx(9.46, rel=0.01)

    assert t2026["total_bess_units"] == pytest.approx(2_190_625, rel=0.001)
    assert t2026["total_capacity_gwh"] == pytest.approx(19.12, rel=0.01)
    assert t2026["total_power_gw"] == pytest.approx(12.16, rel=0.01)

    # The fleet grows in count and in energy, but the mean unit gets SMALLER.
    assert t2024["total_capacity_gwh"] < t2025["total_capacity_gwh"] < t2026["total_capacity_gwh"]
    assert t2024["total_bess_units"] < t2025["total_bess_units"] < t2026["total_bess_units"]
    assert t2024["mean_capacity_kwh"] > t2025["mean_capacity_kwh"] > t2026["mean_capacity_kwh"]


def test_grid_exchange_shift_formula():
    """Verify Grid Exchange Shift definition (Shift = Net Grid Exchange Opt - Rule)."""
    opt = np.array([-500.0, 0.0, 200.0])
    rule = np.array([100.0, -300.0, 200.0])
    shift = compute_grid_exchange_shift(opt, rule)
    # -500 - 100 = -600 (additional export / lower import)
    # 0 - (-300) = +300 (restraint: avoided low-value export)
    # 200 - 200 = 0
    np.testing.assert_allclose(shift, [-600.0, 300.0, 0.0])


def test_fossil_gas_displacement_and_benchmark_days():
    """Verify fossil gas displacement logic across the thesis representative case study days."""
    assert MARGINAL_COST_BESS_EUR_MWH == pytest.approx(48.95)

    # Winter day: 16.01.2024 at 09:00: -692.49 MWh shift, 15,445 MWh gas generation -> ~4.5% reduction
    winter = REPRESENTATIVE_DAYS["winter"]
    res = evaluate_fossil_gas_displacement(
        winter["peak_shift_mwh"],
        winter["fossil_gas_mwh"],
    )
    assert res["displacement_pct"] == pytest.approx(winter["gas_reduction_pct"], abs=0.1)

    # Transition season: 15.03.2024 at 08:00: -2493.10 MWh shift, 12,719 MWh gas -> ~19.6% reduction
    trans = REPRESENTATIVE_DAYS["transition"]
    res_trans = evaluate_fossil_gas_displacement(
        trans["peak_shift_mwh"],
        trans["fossil_gas_mwh"],
    )
    assert res_trans["displacement_pct"] == pytest.approx(trans["gas_reduction_pct"], abs=0.1)

    # Summer day: 01.07.2024 at 19:00: -5264.31 MWh shift, 5,427 MWh gas -> 97.0% reduction
    summer = REPRESENTATIVE_DAYS["summer"]
    res_summer = evaluate_fossil_gas_displacement(
        summer["peak_shift_mwh"],
        summer["fossil_gas_mwh"],
    )
    assert res_summer["displacement_pct"] == pytest.approx(summer["gas_reduction_pct"], abs=0.1)

    # Scarcity peak: 12.12.2024 at 17:00: -5775.91 MWh shift, 19,117 MWh gas -> ~30.2% reduction
    scarcity = REPRESENTATIVE_DAYS["scarcity_peak"]
    res_scarcity = evaluate_fossil_gas_displacement(
        scarcity["peak_shift_mwh"],
        scarcity["fossil_gas_mwh"],
    )
    assert res_scarcity["displacement_pct"] == pytest.approx(scarcity["gas_reduction_pct"], abs=0.2)


@pytest.mark.skipif(not PRICE_CACHE.exists(), reason="price cache absent; run prosumer build-data")
@pytest.mark.parametrize("day, extreme, quoted", [
    ("negative_price", "min", "-135 EUR/MWh"),
    ("scarcity_peak", "max", "936 EUR/MWh"),
])
def test_stress_day_descriptions_quote_the_day_ahead_extreme(day, extreme, quoted):
    """The two price-stress days quote the day's extreme DE-LU day-ahead price, at peak_hour.

    Regression: the negative-price day said -50 EUR/MWh; the cached Energy-Charts series has
    -135.45 EUR/MWh on 2024-05-12, at 12:00 on the thesis clock (CET, UTC+1).
    """
    from prosumer.data.loaders import fetch_spot_prices

    spot = fetch_spot_prices("2024-01-01", "2026-09-18", cache_dir=PRICE_CACHE.parent)
    d = REPRESENTATIVE_DAYS[day]
    eur_mwh = spot["spot_eur_per_kwh"].tz_convert("Etc/GMT-1").loc[d["date"]] * 1000.0
    value = eur_mwh.min() if extreme == "min" else eur_mwh.max()
    when = eur_mwh.idxmin() if extreme == "min" else eur_mwh.idxmax()
    assert f"{value:.0f} EUR/MWh" == quoted
    assert quoted in d["description"]
    assert when.hour == d["peak_hour"]

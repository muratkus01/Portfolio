"""Tests for Germany-wide system-level impact analysis and merit-order displacement."""
from __future__ import annotations

import numpy as np
import pytest

from prosumer.experiments.national_impact import (
    FEDERAL_STATES,
    MARGINAL_COST_BESS_EUR_MWH,
    REPRESENTATIVE_DAYS,
    compute_grid_exchange_shift,
    evaluate_fossil_gas_displacement,
    get_national_totals,
)


def test_federal_states_completeness():
    """Verify all 16 German federal states are configured with coordinates and fleet data."""
    assert len(FEDERAL_STATES) == 16
    for code, state in FEDERAL_STATES.items():
        assert state.latitude > 45.0
        assert state.longitude > 5.0
        assert state.bess_count_2024 > 0
        assert state.avg_capacity_kwh_2024 > 5.0
        assert state.avg_power_kw_2024 > 3.0


def test_national_totals_2024_matches_thesis():
    """Verify 2024 national aggregation matches Master's Thesis Table 10-11 values."""
    totals = get_national_totals(2024)
    # Thesis Table 10-11: 1,116,967 units, 9,972.33 MWh (9.972 GWh), 6,167 MW (6.167 GW)
    assert totals["total_bess_units"] == 1116967
    assert totals["total_capacity_gwh"] == pytest.approx(9.972, abs=0.01)
    assert totals["total_power_gw"] == pytest.approx(6.167, abs=0.01)
    assert totals["mean_capacity_kwh"] == pytest.approx(8.68, abs=0.05)
    assert totals["mean_power_kw"] == pytest.approx(5.40, abs=0.05)


def test_national_totals_2025_and_2026_expansion():
    """Verify updated 2025 and 2026 fleet statistics from battery-charts.de reflect market growth."""
    t2024 = get_national_totals(2024)
    t2025 = get_national_totals(2025)
    t2026 = get_national_totals(2026)

    # 2025: >2M residential systems, >18 GWh capacity, >11.5 GW power
    assert t2025["total_bess_units"] > 2_000_000
    assert t2025["total_capacity_gwh"] > 18.0
    assert t2025["total_power_gw"] > 11.5
    assert t2025["total_capacity_gwh"] > t2024["total_capacity_gwh"]

    # 2026: ~2.5M residential systems, ~22+ GWh capacity, ~14+ GW power
    assert t2026["total_bess_units"] >= 2_400_000
    assert t2026["total_capacity_gwh"] >= 22.0
    assert t2026["total_power_gw"] >= 14.0
    assert t2026["total_capacity_gwh"] > t2025["total_capacity_gwh"]


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

    # Transition season: 15.03.2024 at 08:00: -2493.10 MWh shift, 7,005 MWh gas -> ~19.6% reduction
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

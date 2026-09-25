"""Tests for thesis sensitivity analysis engine (Types 1 and 2)."""
from __future__ import annotations

import numpy as np
import pytest

from prosumer.experiments.sensitivity import (
    compute_annual_fixed_cost,
    compute_capex,
    filter_feasible_configurations,
    generate_3d_design_space,
    is_configuration_feasible,
    run_type1_sensitivity,
    run_type2_fixed_pv_sensitivity,
)


def test_3d_grid_reduction_exact_count():
    """Verify that ratio filters reduce 2,744 combinations to exactly 1,016 configurations (Table 3-4)."""
    raw_space = generate_3d_design_space(pv_range=(2, 15), bess_range=(2, 15), inv_range=(2, 15))
    assert len(raw_space) == 14 * 14 * 14  # 2,744 combinations

    feasible = filter_feasible_configurations(raw_space)
    assert len(feasible) == 1016, f"Expected exactly 1,016 configurations, got {len(feasible)}"


def test_ratio_constraints_satisfied():
    """Verify every feasible configuration satisfies PVS, EPR, and Inverter Loading in [0.5, 2.0]."""
    feasible = filter_feasible_configurations()
    for pv, bess, inv in feasible:
        pvs = pv / bess
        epr = bess / inv
        il = pv / inv
        assert 0.5 <= pvs <= 2.0, f"PVS out of bounds for ({pv}, {bess}, {inv}): {pvs}"
        assert 0.5 <= epr <= 2.0, f"EPR out of bounds for ({pv}, {bess}, {inv}): {epr}"
        assert 0.5 <= il <= 2.0, f"IL out of bounds for ({pv}, {bess}, {inv}): {il}"


def test_capex_model_reproduction():
    """Verify CAPEX formulation against Master's Thesis Table 3-2 reference household."""
    # 8 kWp PV, 9.37 kWh BESS, 5.63 kW inverter
    capex = compute_capex(pv_kwp=8.0, bess_kwh=9.37, inverter_kw=5.63, scenario="optimized")
    # Expected: 8*130 + 9.37*350 + 5.63*190 + 135 + 500 + 800 + 2000 + 2500 = 11,324.2 EUR
    expected_total = (
        8.0 * 130.0 + 9.37 * 350.0 + 5.63 * 190.0 + 135.0 + 500.0 + 800.0 + 2000.0 + 2500.0
    )
    assert capex.total_capex_eur == pytest.approx(expected_total, abs=0.1)
    assert capex.total_capex_eur == pytest.approx(11324.2, abs=0.5)


def test_type1_sensitivity_ranking():
    """Verify Type 1 sensitivity ranks all 1,016 configurations and identifies optimal designs."""
    df = run_type1_sensitivity()
    assert len(df) == 1016
    assert "roce" in df.columns
    assert "rank" in df.columns

    # Verify ranked in descending order of ROCE
    assert (df["roce"].diff().dropna() <= 1e-6).all()

    # Top configurations should have maximum PV capacity (15 kWp) as established in Section 4.2.1
    top_3 = df.head(3)
    assert (top_3["pv_kwp"] == 15).all()

    # Configuration 15_8_8 is the top 1 optimal configuration (ROCE ~ 0.090)
    top_1 = df.iloc[0]
    assert top_1["config_id"] == "15_8_8"
    assert top_1["roce"] == pytest.approx(0.090, abs=0.005)


def test_type2_fixed_pv_sensitivity():
    """Verify Type 2 targeted sensitivity for 5 kWp, 10 kWp, and 15 kWp."""
    for pv_fixed, expected_opt_bess, expected_opt_inv in [
        (5.0, 10, 5),
        (10.0, 10, 5),
        (15.0, 8, 8),
    ]:
        res = run_type2_fixed_pv_sensitivity(pv_fixed)
        assert res["valid_count"] > 0
        opt = res["optimum"]
        assert opt["bess_kwh"] == expected_opt_bess
        assert opt["inverter_kw"] == expected_opt_inv
        assert 0.0 < opt["roce"] < 0.20

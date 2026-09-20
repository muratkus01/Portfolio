"""Tests for prosumer economics and sizing grid evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prosumer.config import SiteConfig
from prosumer.experiments.sizing import run_sizing_grid
from prosumer.reporting.economics import compute_investment_metrics
from prosumer.thesis import COSTS_OPTIMIZED, THESIS_SITE


def test_investment_metrics_calculation():
    # 9.37 kWh, 5.63 kW inverter: capex = 9.37 * 350 + 5.63 * 190 = 3279.5 + 1069.7 = 4349.2 EUR
    site = THESIS_SITE
    metrics = compute_investment_metrics(
        cost_annual_nobat=1500.0,
        cost_annual_bat=1000.0,
        site=site,
        cost_profile=COSTS_OPTIMIZED,
        annual_cycles=250.0,
    )
    assert metrics.capex_eur == pytest.approx(site.e_bess * 350.0 + site.p_inv * 190.0)
    assert metrics.annual_savings_eur == pytest.approx(500.0)
    assert metrics.simple_payback_years == pytest.approx(metrics.capex_eur / 500.0)
    assert metrics.roce_pct == pytest.approx((500.0 / metrics.capex_eur) * 100.0)
    assert metrics.opt_profit_annual_eur == pytest.approx(500.0 - COSTS_OPTIMIZED.annual_fixed)


def test_sizing_grid_runs_on_synthetic():
    idx = pd.date_range("2024-06-01", periods=96, freq="15min", tz="UTC")
    h = np.arange(96) * 0.25 % 24
    load = 0.4 + 0.8 * np.exp(-((h - 19) ** 2) / 6)
    pv = 4.0 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    spot = 0.08 + 0.04 * np.sin(2 * np.pi * (h - 18) / 24)

    df = pd.DataFrame({"load_kw": load, "pv_kw": pv, "spot_eur_per_kwh": spot}, index=idx)
    res = run_sizing_grid(df, capacities=(5.0, 10.0), inverters=(3.0, 5.0))

    assert len(res) == 4
    assert set(res.columns) >= {"battery_kwh", "inverter_kw", "capex_eur", "annual_savings_eur",
                                "simple_payback_years", "roce_pct"}
    assert (res["battery_kwh"] > 0).all()
    assert (res["capex_eur"] > 0).all()
    assert (res["annual_savings_eur"] >= 0).all()

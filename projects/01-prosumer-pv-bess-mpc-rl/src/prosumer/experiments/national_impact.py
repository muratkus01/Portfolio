"""Germany-Wide System-Level Impact Analysis of BESS Optimization.

Directly implements the national scaling and merit-order displacement methodology from
Murat Kuş's M.Sc. thesis:
"Techno-Economic Assessment of Residential BESS with PV and Dynamic Tariffs" (Section 3.6 & 4.3).

Features:
1. Meta-Household Framework across all 16 German federal states (Bundesländer).
2. Sizing and fleet statistics updated across three data vintages:
   - 2024 Baseline (1.117M residential BESS, 9.97 GWh, 6.17 GW) from MaStR Table 10-11
   - 2025 Actual (2.080M residential BESS, 18.2 GWh, 11.6 GW) from battery-charts.de Table 10-2
   - 2026 Expansion (~2.500M residential BESS, 22.2 GWh, 14.2 GW) from latest battery-charts.de
3. Calculation of Grid Exchange Shift:
   Grid Exchange Shift_t = Net Grid Exchange_t^Optimized - Net Grid Exchange_t^RuleBased
4. Merit-order overlay displacing marginal fossil gas generation at €48.95/MWh.
5. Analysis of the five representative seasonal and market stress days:
   - 16.01.2024 (Winter Day)
   - 15.03.2024 (Transition Season)
   - 01.07.2024 (Summer Day)
   - 12.05.2024 (Extreme Low / Negative Wholesale Prices)
   - 12.12.2024 (Extreme High Scarcity Wholesale Prices)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- State-Level Statistics
@dataclass(frozen=True)
class StateFleetData:
    name: str
    capital: str
    latitude: float
    longitude: float
    # 2024 baseline (01.01.2024, MaStR / Table 10-11)
    bess_count_2024: int
    avg_capacity_kwh_2024: float
    avg_power_kw_2024: float
    # 2025 actual (22.09.2025, battery-charts.de / Table 10-2)
    bess_count_2025: int
    avg_capacity_kwh_2025: float
    avg_power_kw_2025: float
    # 2026 expansion (Q1-Q3 2026, battery-charts.de / MaStR)
    bess_count_2026: int
    avg_capacity_kwh_2026: float
    avg_power_kw_2026: float


FEDERAL_STATES: dict[str, StateFleetData] = {
    "BW": StateFleetData(
        name="Baden-Württemberg", capital="Stuttgart", latitude=48.7823, longitude=9.1770,
        bess_count_2024=180822, avg_capacity_kwh_2024=8.98, avg_power_kw_2024=5.37,
        bess_count_2025=315000, avg_capacity_kwh_2025=9.15, avg_power_kw_2025=5.50,
        bess_count_2026=375000, avg_capacity_kwh_2026=9.20, avg_power_kw_2026=5.58,
    ),
    "BY": StateFleetData(
        name="Bavaria", capital="Munich", latitude=48.1374, longitude=11.5753,
        bess_count_2024=236713, avg_capacity_kwh_2024=9.37, avg_power_kw_2024=5.63,
        bess_count_2025=460000, avg_capacity_kwh_2025=9.50, avg_power_kw_2025=5.80,
        bess_count_2026=550000, avg_capacity_kwh_2026=9.55, avg_power_kw_2026=5.85,
    ),
    "BE": StateFleetData(
        name="Berlin", capital="Berlin", latitude=52.5244, longitude=13.4105,
        bess_count_2024=10841, avg_capacity_kwh_2024=8.63, avg_power_kw_2024=5.07,
        bess_count_2025=21000, avg_capacity_kwh_2025=8.80, avg_power_kw_2025=5.25,
        bess_count_2026=26000, avg_capacity_kwh_2026=8.90, avg_power_kw_2026=5.30,
    ),
    "BB": StateFleetData(
        name="Brandenburg", capital="Potsdam", latitude=52.3989, longitude=13.0657,
        bess_count_2024=38706, avg_capacity_kwh_2024=8.65, avg_power_kw_2024=5.00,
        bess_count_2025=85000, avg_capacity_kwh_2025=8.80, avg_power_kw_2025=5.30,
        bess_count_2026=105000, avg_capacity_kwh_2026=8.90, avg_power_kw_2026=5.40,
    ),
    "HB": StateFleetData(
        name="Bremen", capital="Bremen", latitude=53.0752, longitude=8.8078,
        bess_count_2024=2152, avg_capacity_kwh_2024=7.82, avg_power_kw_2024=4.69,
        bess_count_2025=7500, avg_capacity_kwh_2025=8.00, avg_power_kw_2025=4.90,
        bess_count_2026=9500, avg_capacity_kwh_2026=8.15, avg_power_kw_2026=5.00,
    ),
    "HH": StateFleetData(
        name="Hamburg", capital="Hamburg", latitude=53.5753, longitude=10.0153,
        bess_count_2024=5286, avg_capacity_kwh_2024=8.19, avg_power_kw_2024=4.95,
        bess_count_2025=11500, avg_capacity_kwh_2025=8.35, avg_power_kw_2025=5.15,
        bess_count_2026=14500, avg_capacity_kwh_2026=8.45, avg_power_kw_2026=5.25,
    ),
    "HE": StateFleetData(
        name="Hesse", capital="Wiesbaden", latitude=50.0826, longitude=8.2493,
        bess_count_2024=68017, avg_capacity_kwh_2024=8.90, avg_power_kw_2024=5.66,
        bess_count_2025=135000, avg_capacity_kwh_2025=9.05, avg_power_kw_2025=5.75,
        bess_count_2026=165000, avg_capacity_kwh_2026=9.10, avg_power_kw_2026=5.80,
    ),
    "MV": StateFleetData(
        name="Mecklenburg-Western Pomerania", capital="Schwerin", latitude=53.6296, longitude=11.4148,
        bess_count_2024=15744, avg_capacity_kwh_2024=8.59, avg_power_kw_2024=5.50,
        bess_count_2025=34000, avg_capacity_kwh_2025=8.75, avg_power_kw_2025=5.70,
        bess_count_2026=42000, avg_capacity_kwh_2026=8.85, avg_power_kw_2026=5.75,
    ),
    "NI": StateFleetData(
        name="Lower Saxony", capital="Hannover", latitude=52.3759, longitude=9.7320,
        bess_count_2024=137876, avg_capacity_kwh_2024=8.54, avg_power_kw_2024=5.46,
        bess_count_2025=250000, avg_capacity_kwh_2025=8.75, avg_power_kw_2025=5.60,
        bess_count_2026=305000, avg_capacity_kwh_2026=8.85, avg_power_kw_2026=5.70,
    ),
    "NW": StateFleetData(
        name="North Rhine-Westphalia", capital="Düsseldorf", latitude=51.2217, longitude=6.7762,
        bess_count_2024=217411, avg_capacity_kwh_2024=8.93, avg_power_kw_2024=5.63,
        bess_count_2025=410000, avg_capacity_kwh_2025=9.10, avg_power_kw_2025=5.75,
        bess_count_2026=490000, avg_capacity_kwh_2026=9.15, avg_power_kw_2026=5.82,
    ),
    "RP": StateFleetData(
        name="Rhineland-Palatinate", capital="Mainz", latitude=49.9842, longitude=8.2791,
        bess_count_2024=65936, avg_capacity_kwh_2024=8.92, avg_power_kw_2024=5.82,
        bess_count_2025=120000, avg_capacity_kwh_2025=9.00, avg_power_kw_2025=5.95,
        bess_count_2026=145000, avg_capacity_kwh_2026=9.05, avg_power_kw_2026=6.00,
    ),
    "SL": StateFleetData(
        name="Saarland", capital="Saarbrücken", latitude=49.2333, longitude=7.0000,
        bess_count_2024=11559, avg_capacity_kwh_2024=8.59, avg_power_kw_2024=6.10,
        bess_count_2025=28000, avg_capacity_kwh_2025=8.75, avg_power_kw_2025=6.25,
        bess_count_2026=34000, avg_capacity_kwh_2026=8.80, avg_power_kw_2026=6.30,
    ),
    "SN": StateFleetData(
        name="Saxony", capital="Dresden", latitude=51.0509, longitude=13.7383,
        bess_count_2024=40041, avg_capacity_kwh_2024=9.20, avg_power_kw_2024=5.45,
        bess_count_2025=85000, avg_capacity_kwh_2025=9.35, avg_power_kw_2025=5.60,
        bess_count_2026=105000, avg_capacity_kwh_2026=9.40, avg_power_kw_2026=5.68,
    ),
    "ST": StateFleetData(
        name="Saxony-Anhalt", capital="Magdeburg", latitude=52.1277, longitude=11.6292,
        bess_count_2024=25579, avg_capacity_kwh_2024=8.51, avg_power_kw_2024=5.46,
        bess_count_2025=58000, avg_capacity_kwh_2025=8.65, avg_power_kw_2025=5.60,
        bess_count_2026=70000, avg_capacity_kwh_2026=8.75, avg_power_kw_2026=5.68,
    ),
    "SH": StateFleetData(
        name="Schleswig-Holstein", capital="Kiel", latitude=54.3213, longitude=10.1394,
        bess_count_2024=35740, avg_capacity_kwh_2024=8.22, avg_power_kw_2024=5.10,
        bess_count_2025=105000, avg_capacity_kwh_2025=8.45, avg_power_kw_2025=5.30,
        bess_count_2026=130000, avg_capacity_kwh_2026=8.55, avg_power_kw_2026=5.40,
    ),
    "TH": StateFleetData(
        name="Thuringia", capital="Erfurt", latitude=50.9848, longitude=11.0299,
        bess_count_2024=24544, avg_capacity_kwh_2024=8.77, avg_power_kw_2024=5.53,
        bess_count_2025=63000, avg_capacity_kwh_2025=8.90, avg_power_kw_2025=5.70,
        bess_count_2026=78000, avg_capacity_kwh_2026=9.00, avg_power_kw_2026=5.75,
    ),
}

MARGINAL_COST_BESS_EUR_MWH = 48.95  # Table 10-13: PV-charged BESS marginal discharge cost


def get_national_totals(year: int = 2024) -> dict[str, float]:
    """Calculate aggregated national residential storage capacity and inverter power."""
    total_count = 0
    total_capacity_mwh = 0.0
    total_power_mw = 0.0

    state_capacities = []
    state_powers = []

    for state in FEDERAL_STATES.values():
        if year == 2024:
            count = state.bess_count_2024
            cap = state.avg_capacity_kwh_2024
            pwr = state.avg_power_kw_2024
        elif year == 2025:
            count = state.bess_count_2025
            cap = state.avg_capacity_kwh_2025
            pwr = state.avg_power_kw_2025
        else:
            count = state.bess_count_2026
            cap = state.avg_capacity_kwh_2026
            pwr = state.avg_power_kw_2026

        state_capacities.append(cap)
        state_powers.append(pwr)
        total_count += count
        total_capacity_mwh += (count * cap) / 1000.0
        total_power_mw += (count * pwr) / 1000.0

    return {
        "year": year,
        "total_bess_units": total_count,
        "total_capacity_mwh": round(total_capacity_mwh, 2),
        "total_capacity_gwh": round(total_capacity_mwh / 1000.0, 3),
        "total_power_mw": round(total_power_mw, 2),
        "total_power_gw": round(total_power_mw / 1000.0, 3),
        "weighted_mean_capacity_kwh": round(total_capacity_mwh * 1000.0 / total_count, 3),
        "weighted_mean_power_kw": round(total_power_mw * 1000.0 / total_count, 3),
        # Simple unweighted mean of state averages (reported in Table 10-11 summary row)
        "simple_mean_capacity_kwh": round(float(np.mean(state_capacities)), 5),
        "simple_mean_power_kw": round(float(np.mean(state_powers)), 5),
        "mean_capacity_kwh": round(float(np.mean(state_capacities)), 5),
        "mean_power_kw": round(float(np.mean(state_powers)), 5),
    }


def compute_grid_exchange_shift(
    net_grid_exchange_opt: np.ndarray,
    net_grid_exchange_rule: np.ndarray,
) -> np.ndarray:
    """Compute hourly Grid Exchange Shift (Equation 3-19):

    Shift_t = Net Grid Exchange_t^Optimized - Net Grid Exchange_t^Rule-Based
    A negative shift during morning/evening peaks indicates additional clean export (displacing fossil gas).
    A positive shift during midday indicates restraint or absorption of excess PV generation.
    """
    return np.asarray(net_grid_exchange_opt) - np.asarray(net_grid_exchange_rule)


def evaluate_fossil_gas_displacement(
    grid_exchange_shift_mwh: float,
    fossil_gas_generation_mwh: float,
    marginal_cost_bess: float = MARGINAL_COST_BESS_EUR_MWH,
    marginal_cost_gas: float = 85.0,
) -> dict[str, float]:
    """Calculate merit-order displacement of fossil gas peaking units by optimized BESS exports.

    Displacement occurs when the battery discharge marginal cost (48.95 EUR/MWh) is below
    the marginal cost of operating fossil gas units.
    """
    if grid_exchange_shift_mwh >= 0 or fossil_gas_generation_mwh <= 0:
        return {
            "displaced_gas_mwh": 0.0,
            "displacement_pct": 0.0,
            "cost_saving_eur": 0.0,
        }

    # Absolute export volume added by optimization
    export_added = abs(grid_exchange_shift_mwh)
    displaced = min(export_added, fossil_gas_generation_mwh)
    pct = (displaced / fossil_gas_generation_mwh) * 100.0
    cost_diff = max(0.0, marginal_cost_gas - marginal_cost_bess)
    saving = displaced * cost_diff

    return {
        "displaced_gas_mwh": round(displaced, 2),
        "displacement_pct": round(pct, 2),
        "cost_saving_eur": round(saving, 2),
    }


# --------------------------------------------------------------------------- Five Benchmark Days Data
REPRESENTATIVE_DAYS = {
    "winter": {
        "date": "2024-01-16",
        "description": "Typical winter day with high heating demand and low solar irradiance",
        "peak_hour": 9,
        "peak_shift_mwh": -692.49,
        "fossil_gas_mwh": 15445.0,
        "gas_reduction_pct": 4.5,
    },
    "transition": {
        "date": "2024-03-15",
        "description": "Transition season day with balanced solar generation and demand ramp",
        "peak_hour": 8,
        "peak_shift_mwh": -2493.10,
        "fossil_gas_mwh": 12719.0,
        "gas_reduction_pct": 19.6,
    },
    "summer": {
        "date": "2024-07-01",
        "description": "Peak summer day with strong midday solar and steep evening peak",
        "peak_hour": 19,
        "peak_shift_mwh": -5264.31,
        "fossil_gas_mwh": 5427.0,
        "gas_reduction_pct": 97.0,
    },
    "negative_price": {
        "date": "2024-05-12",
        "description": "Extreme renewable oversupply with midday spot prices reaching -50 EUR/MWh",
        "peak_hour": 12,
        "peak_shift_mwh": 5448.09,  # positive shift indicates absorption / export withholding
        "fossil_gas_mwh": 2335.0,
        "gas_reduction_pct": 0.0,
    },
    "scarcity_peak": {
        "date": "2024-12-12",
        "description": "Winter Dunkelflaute scarcity peak with spot prices spiking to 936 EUR/MWh",
        "peak_hour": 17,
        "peak_shift_mwh": -5775.91,
        "fossil_gas_mwh": 19117.0,
        "gas_reduction_pct": 30.2,
    },
}

"""Germany-Wide System-Level Impact Analysis of BESS Optimization.

Implements the national fleet scaling and the merit-order displacement accounting from
Murat Kuş's M.Sc. thesis:
"Techno-Economic Assessment of Residential BESS with PV and Dynamic Tariffs" (Section 3.6 & 4.3).
No dispatch runs here: the representative-day shifts are thesis values (item 5).

Features:
1. Fleet table of all 16 German federal states (Bundesländer): the MaStR home-storage count
   per state and the Germany-wide mean size. The thesis's meta-household dispatch is not in
   this repository.
2. Fleet statistics read from the MaStR register at three 1 January snapshots, not
   extrapolated. Source and caveats: `data/raw/mastr/SOURCE.md`.
   - 2024-01-01: 1,119,240 home storages, 10.03 GWh, 6.19 GW
   - 2025-01-01: 1,670,776 home storages, 14.90 GWh, 9.46 GW
   - 2026-01-01: 2,190,625 home storages, 19.12 GWh, 12.16 GW
   The 2024 figures reproduce the thesis table (1.117M, 9.97 GWh, 6.17 GW) to within 0.6 %.
   The 2025 and 2026 figures replace a hand-written extrapolation that overstated the fleet
   by 24 % and 14 %.
   These are lithium-ion only, which is 96.8 % of all home storages in 2024 and 97.2 % in
   2026; the register counts the remainder under other chemistries.
   The download ends 2026-01-13, so "2026" is a January snapshot and not a full year.
3. Calculation of Grid Exchange Shift (the formula only; nothing here computes the two
   dispatch series):
   Grid Exchange Shift_t = Net Grid Exchange_t^Optimized - Net Grid Exchange_t^RuleBased
4. Merit-order accounting on the thesis Table 10-13 shifts: added battery export (discharge
   cost €48.95/MWh) replaces fossil gas (€85/MWh) one to one, up to the gas generation given.
5. The five representative seasonal and market stress days, with the thesis's national
   shifts and gas volumes copied in as fixed values (`REPRESENTATIVE_DAYS`):
   - 16.01.2024 (Winter Day)
   - 15.03.2024 (Transition Season)
   - 01.07.2024 (Summer Day)
   - 12.05.2024 (Extreme Low / Negative Wholesale Prices)
   - 12.12.2024 (Extreme High Scarcity Wholesale Prices)
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import numpy as np
import pandas as pd

from ..data import mastr


# --------------------------------------------------------------------------- State-Level Statistics
@dataclass(frozen=True)
class StateFleetData:
    """Home-storage fleet of one federal state at three annual snapshots.

    Counts come from the MaStR register via `prosumer.data.mastr`; see
    `data/raw/mastr/SOURCE.md`. The mean size is the PUBLISHED Germany-wide mean and is
    therefore the same for every state. That is deliberate:

    A per-state mean would have to be state energy divided by state home count, but the
    per-state energy series covers all lithium-ion segments while the count is home only.
    Home storage is 83.9 % of installed energy in January 2024 and 80.1 % in January 2026,
    and large-scale batteries are not spread evenly, so that quotient is biased high and
    unevenly so: it returns 14.8 kWh for Bremen and 11.3 kWh for Bavaria against a published
    national mean of 8.96 kWh. The thesis figure of 9.37 kWh for Bavaria is reproducible as
    that biased quotient scaled by the national home share (9.44 kWh), which is why it looked
    plausible. It is still an estimator with a state-dependent bias, so it is not used here.

    `prosumer.data.mastr.mean_size_by_state` computes the per-state estimate with its bias
    columns for anyone who wants to look at it.
    """
    name: str
    capital: str
    latitude: float
    longitude: float
    bess_count_2024: int
    avg_capacity_kwh_2024: float
    avg_power_kw_2024: float
    bess_count_2025: int
    avg_capacity_kwh_2025: float
    avg_power_kw_2025: float
    bess_count_2026: int
    avg_capacity_kwh_2026: float
    avg_power_kw_2026: float


# Geography only. Not in the MaStR export, and not data that changes.
_GEO: dict[str, tuple[str, str, float, float]] = {
    "BW": ("Baden-W\u00fcrttemberg", "Stuttgart", 48.7823, 9.1770),
    "BY": ("Bavaria", "Munich", 48.1374, 11.5753),
    "BE": ("Berlin", "Berlin", 52.5244, 13.4105),
    "BB": ("Brandenburg", "Potsdam", 52.3989, 13.0657),
    "HB": ("Bremen", "Bremen", 53.0752, 8.8078),
    "HH": ("Hamburg", "Hamburg", 53.5753, 10.0153),
    "HE": ("Hesse", "Wiesbaden", 50.0826, 8.2493),
    "MV": ("Mecklenburg-Western Pomerania", "Schwerin", 53.6296, 11.4148),
    "NI": ("Lower Saxony", "Hannover", 52.3759, 9.7320),
    "NW": ("North Rhine-Westphalia", "D\u00fcsseldorf", 51.2217, 6.7762),
    "RP": ("Rhineland-Palatinate", "Mainz", 49.9842, 8.2791),
    "SL": ("Saarland", "Saarbr\u00fccken", 49.2333, 7.0000),
    "SN": ("Saxony", "Dresden", 51.0509, 13.7383),
    "ST": ("Saxony-Anhalt", "Magdeburg", 52.1277, 11.6292),
    "SH": ("Schleswig-Holstein", "Kiel", 54.3213, 10.1394),
    "TH": ("Thuringia", "Erfurt", 50.9848, 11.0299),
}

# The snapshots the three annual scenarios refer to. 1 January of each year, which is the
# convention the thesis used. The download ends on 2026-01-13, so 2026 is a January snapshot
# and not a full year; `mastr.coverage()` says so, and the README repeats it.
SNAPSHOTS: dict[int, str] = {2024: "2024-01-01", 2025: "2025-01-01", 2026: "2026-01-01"}


def _load_federal_states() -> dict[str, StateFleetData]:
    """Build the fleet table from the MaStR download. Raises if the files are missing."""
    counts = mastr.count_by_state("Home storage")
    means = mastr.home_storage_mean()
    fields: dict[str, dict[str, object]] = {}
    for year, date in SNAPSHOTS.items():
        ts = pd.Timestamp(date)
        for code in _GEO:
            f = fields.setdefault(code, {})
            f[f"bess_count_{year}"] = int(round(float(counts.loc[ts, code])))
            f[f"avg_capacity_kwh_{year}"] = float(means.loc[ts, "mean_kwh"])
            f[f"avg_power_kw_{year}"] = float(means.loc[ts, "mean_kw"])
    return {
        code: StateFleetData(name=geo[0], capital=geo[1], latitude=geo[2], longitude=geo[3],
                             **fields[code])
        for code, geo in _GEO.items()
    }


@lru_cache(maxsize=1)
def federal_states() -> dict[str, StateFleetData]:
    """The fleet table of all 16 states, read from the MaStR download on first use.

    Read lazily on purpose. The CSVs are gitignored, and a module-level read made every
    import of this module fail on a fresh clone and in CI, which aborted test collection for
    the whole portfolio. Returns {state code: StateFleetData}, e.g. {"BY": ..., "BW": ...}.
    """
    if not mastr.available():
        raise FileNotFoundError(
            f"MaStR fleet files not found under {mastr.RAW}; "
            "download them as listed in data/raw/mastr/SOURCE.md"
        )
    return _load_federal_states()


MARGINAL_COST_BESS_EUR_MWH = 48.95  # Table 10-13: PV-charged BESS marginal discharge cost



def get_national_totals(year: int = 2024) -> dict[str, float]:
    """Calculate aggregated national residential storage capacity and inverter power."""
    total_count = 0
    total_capacity_mwh = 0.0
    total_power_mw = 0.0

    state_capacities = []
    state_powers = []

    for state in federal_states().values():
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
        # Day-ahead minimum -135.45 EUR/MWh at 12:00 CET (Energy-Charts); the test checks it.
        "description": "Extreme renewable oversupply with midday spot prices reaching -135 EUR/MWh",
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

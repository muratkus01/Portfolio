"""Rooftop PV output from irradiance with pvlib.

A PVWatts-type chain: solar position at interval midpoint, plane-of-array irradiance
(Hay-Davies), Faiman cell temperature, PVWatts DC model with a temperature coefficient,
then a lumped system loss. The same function turns ACTUAL weather into the PV the site
produces and FORECAST weather into the PV the controller expects, so forecast error in PV
is exactly the forecast error of the weather model.

Calibration. `system_loss` is set so that the modelled 2024 specific yield equals the
thesis profile (1195.1 kWh/kWp in Munich), which keeps the household's PV scale identical to
the thesis. With Open-Meteo 15-minute irradiance that is a loss of 7.7 %, at the low end of
the usual 8 to 14 % range. The modelled production is centred on solar noon (12:16 CET on
average); the thesis profile is centred about one hour earlier, consistent with UTC
timestamps having been read as CET when it was prepared.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .weather import MUNICH

THESIS_SPECIFIC_YIELD_2024 = 1195.1      # kWh/kWp, PV_Bayern_1 in the thesis input year


@dataclass(frozen=True)
class PVSystem:
    kwp: float = 8.0
    tilt: float = 35.0
    azimuth: float = 180.0               # south
    gamma_pdc: float = -0.004            # 1/K
    system_loss: float = 0.0766          # lumped; calibrated, see module docstring
    latitude: float = MUNICH[0]
    longitude: float = MUNICH[1]
    altitude: float = 520.0


def pv_power(weather: pd.DataFrame, system: PVSystem = PVSystem()) -> pd.Series:
    """AC power in kW on the weather's index (UTC, left-labelled intervals)."""
    import pvlib

    idx = weather.index
    step = idx[1] - idx[0] if len(idx) > 1 else pd.Timedelta("1h")
    loc = pvlib.location.Location(system.latitude, system.longitude, altitude=system.altitude)
    sp = loc.get_solarposition(idx + step / 2)          # sun at the interval midpoint
    sp.index = idx
    poa = pvlib.irradiance.get_total_irradiance(
        system.tilt, system.azimuth, sp["apparent_zenith"], sp["azimuth"],
        weather["dni"], weather["ghi"], weather["dhi"],
        dni_extra=pvlib.irradiance.get_extra_radiation(idx), model="haydavies")
    t_cell = pvlib.temperature.faiman(poa["poa_global"], weather["temp_air"],
                                      weather["wind_speed"] / 3.6)
    pdc = pvlib.pvsystem.pvwatts_dc(poa["poa_global"], t_cell, system.kwp * 1000,
                                    system.gamma_pdc) / 1000.0
    ac = (pdc * (1 - system.system_loss)).clip(lower=0.0, upper=system.kwp)
    return ac.fillna(0.0).rename("pv_kw")


def calibrate_loss(weather_2024: pd.DataFrame, target_yield: float = THESIS_SPECIFIC_YIELD_2024,
                   system: PVSystem = PVSystem()) -> float:
    """System loss that makes the modelled 2024 specific yield equal `target_yield`."""
    base = PVSystem(**{**system.__dict__, "kwp": 1.0, "system_loss": 0.0})
    p = pv_power(weather_2024, base)
    dt = (p.index[1] - p.index[0]).total_seconds() / 3600
    lossless_yield = float(p.sum() * dt)
    return float(np.clip(1 - target_yield / lossless_yield, 0.0, 0.5))

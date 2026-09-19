"""Named configurations for the thesis extension (2024 to 2026, 15 minutes).

The site is the thesis household (`prosumer.thesis.THESIS_SITE`). Two deliberate
differences from the thesis model:

  * every controller optimises against the prices the household actually pays
    (`THESIS_RETAIL_TARIFF`), not against EPEX +/- C_grid as the thesis MILP did;
  * the daily throughput cap is dropped. Wear is priced through C_MBCC on discharged energy
    (as in the thesis objective), which is sufficient: two full cycles per day never pay off
    at that price, and a cap tied to calendar days is ill-defined for windows that start
    mid-day.
"""
from __future__ import annotations

from dataclasses import replace

from .config import RunConfig
from .thesis import THESIS_RETAIL_TARIFF, THESIS_SITE

EXTENSION_SITE = replace(THESIS_SITE, e_throughput_max_per_day=None)

EXTENSION_RUN = RunConfig(
    dt=0.25, horizon_steps=35 * 4, terminal_value=True, use_binaries=False,
    site=EXTENSION_SITE, tariff=THESIS_RETAIL_TARIFF,
)

"""German retail tariff build-up and export remuneration.

The original MILP priced imported and exported energy identically at the day-ahead spot
price. For a German prosumer this is the single most consequential simplification: the retail
import price is several times the export remuneration, and that spread - not spot arbitrage -
is why a home battery is economic at all.

This module turns a spot price series into the two price series the rest of the package uses:

    price_import[t]   EUR/kWh paid for each kWh drawn from the grid
    price_export[t]   EUR/kWh received for each kWh fed into the grid

Both are marginal prices at the quarter-hour. Fixed annual components (basic charge,
s14a Module 1 flat reduction) do not affect the optimal dispatch and are reported separately
by the evaluation module rather than smeared into the marginal price.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TariffConfig


def network_charge_series(index: pd.DatetimeIndex, cfg: TariffConfig) -> np.ndarray:
    """Energy component of the network charge, EUR/kWh, per step.

    s14a EnWG modules:
      module_1 - flat annual reduction; marginal price unchanged (returned as the base value)
      module_2 - percentage reduction on this component
      module_3 - time-variable: low / standard / high windows by hour of local day
    """
    base = np.full(len(index), cfg.network_charge, dtype=float)

    if cfg.para_14a_module == "module_2":
        return base * (1.0 - cfg.module_2_reduction)

    if cfg.para_14a_module == "module_3":
        local = index.tz_convert("Europe/Berlin") if index.tz is not None else index
        hours = np.asarray(local.hour)
        f_low, f_std, f_high = cfg.module_3_factors
        factors = np.full(len(index), f_std, dtype=float)
        factors[np.isin(hours, cfg.module_3_low_hours)] = f_low
        factors[np.isin(hours, cfg.module_3_high_hours)] = f_high
        return base * factors

    # "none" and "module_1" both leave the marginal network charge unchanged
    return base


def import_price(spot: np.ndarray, index: pd.DatetimeIndex, cfg: TariffConfig) -> np.ndarray:
    """Full retail import price, EUR/kWh, VAT inclusive."""
    energy = np.asarray(spot, dtype=float) if cfg.spot_passthrough \
        else np.full(len(spot), cfg.fixed_energy_price, dtype=float)
    net = energy + cfg.supplier_margin + network_charge_series(index, cfg) \
        + cfg.levies + cfg.electricity_tax
    return net * (1.0 + cfg.vat)


def export_price(spot: np.ndarray, cfg: TariffConfig) -> np.ndarray:
    """Export remuneration, EUR/kWh.

    Modes:
      feed_in_tariff - fixed EEG rate, independent of the spot price
      market_premium - spot-linked, less the direct-marketing fee
      spot           - raw spot price (the original model's implicit assumption)

    The negative-price rule (EEG s51 as tightened for new plants) suspends remuneration in
    any step with a negative spot price. Note that under `spot` mode a negative price already
    means paying to export; the rule floors that at zero, which is why a plant subject to it
    prefers to curtail or charge rather than export.
    """
    spot = np.asarray(spot, dtype=float)
    if cfg.export_mode == "feed_in_tariff":
        price = np.full(len(spot), cfg.feed_in_tariff, dtype=float)
    elif cfg.export_mode == "market_premium":
        price = spot - cfg.market_premium_fee
    else:
        price = spot.copy()

    if cfg.negative_price_rule == "new_2025":
        price = np.where(spot < 0.0, 0.0, price)
    return price


def build_prices(df: pd.DataFrame, cfg: TariffConfig,
                 spot_col: str = "spot_eur_per_kwh") -> pd.DataFrame:
    """Attach `price_import` and `price_export` columns to a time-indexed frame."""
    out = df.copy()
    spot = out[spot_col].to_numpy(dtype=float)
    out["price_import"] = import_price(spot, out.index, cfg)
    out["price_export"] = export_price(spot, cfg)
    return out


def describe(cfg: TariffConfig, spot_mean: float) -> str:
    """One-line human-readable summary of the tariff, for run logs and reports."""
    imp = (spot_mean + cfg.supplier_margin + cfg.network_charge + cfg.levies
           + cfg.electricity_tax) * (1 + cfg.vat)
    exp = cfg.feed_in_tariff if cfg.export_mode == "feed_in_tariff" else spot_mean
    return (f"import ~{imp:.4f} EUR/kWh | export ~{exp:.4f} EUR/kWh | "
            f"spread {imp - exp:.4f} | export_mode={cfg.export_mode} | "
            f"s14a={cfg.para_14a_module} | neg_price_rule={cfg.negative_price_rule}")

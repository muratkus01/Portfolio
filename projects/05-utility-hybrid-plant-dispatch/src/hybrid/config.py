"""Configuration for the co-located wind + PV + battery plant.

The defining feature of the asset is that the grid connection is deliberately UNDERSIZED
relative to the sum of nameplate ratings, because wind and PV peaks rarely coincide. That
undersizing is both the source of the value and the source of the difficulty: when the peaks
do coincide, something must give - curtail, or charge the battery - and the right answer
depends on price, state of charge, forecast and remuneration regime.

Sign convention:
    battery p_bat > 0  DISCHARGING (adds to export)
                < 0    CHARGING
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class PlantConfig:
    wind_mw: float = 50.0
    pv_mw: float = 30.0
    bess_mw: float = 20.0
    bess_mwh: float = 40.0
    conn_mw: float = 60.0            # point-of-interconnection export limit

    soc_min_frac: float = 0.05
    soc_max_frac: float = 0.95
    soc_init_frac: float = 0.50
    eta_c: float = 0.95
    eta_d: float = 0.95
    c_deg_eur_mwh: float = 3.0       # EUR per MWh of throughput

    # Whether the battery may charge FROM THE GRID or only from co-located generation.
    # This is a regulatory and metering question, not a technical one, and the answer
    # materially changes the business case - hence a switch rather than an assumption.
    grid_charging_allowed: bool = False

    ramp_mw_per_step: float | None = None   # None = unlimited (inverter-coupled plant)

    @property
    def installed_mw(self) -> float:
        return self.wind_mw + self.pv_mw

    @property
    def overbuild_ratio(self) -> float:
        """Installed generation divided by export capacity. >1 means curtailment happens."""
        return self.installed_mw / self.conn_mw

    @property
    def soc_min(self) -> float:
        return self.bess_mwh * self.soc_min_frac

    @property
    def soc_max(self) -> float:
        return self.bess_mwh * self.soc_max_frac

    @property
    def soc_init(self) -> float:
        return self.bess_mwh * self.soc_init_frac


@dataclass(frozen=True)
class MarketConfig:
    """EEG direct marketing, the negative-price rule, and imbalance settlement."""

    # --- EEG market premium ---
    # Revenue = market price + premium, where premium = anzulegender Wert - monthly market
    # value. The MONTHLY reference is what makes the objective non-separable across the month;
    # the rolling-horizon controller carries a month-to-date accumulator for this reason.
    eeg_enabled: bool = True
    anzulegender_wert_wind: float = 73.5     # EUR/MWh
    anzulegender_wert_pv: float = 68.0       # EUR/MWh
    direct_marketing_fee: float = 3.0        # EUR/MWh

    # --- negative-price rule (EEG s51, tightened by the 2025 Solarspitzengesetz) ---
    # "none"     : premium always paid (older vintages)
    # "six_hour" : premium suspended after 6 consecutive hours of negative prices
    # "new_2025" : premium suspended in ANY quarter-hour with a negative price
    negative_price_rule: str = "new_2025"

    # --- imbalance ---
    imbalance_enabled: bool = True
    rebap_sigma: float = 55.0                # EUR/MWh, heavy-tailed around the spot price

    # --- optional aFRR participation by the battery ---
    afrr_enabled: bool = False
    afrr_capacity_eur_mw_h: float = 10.0
    afrr_block_steps: int = 16

    # --- redispatch (s13a EnWG) ---
    redispatch_prob_per_step: float = 0.001
    redispatch_depth: float = 0.5            # fraction of export capacity ordered away

    def __post_init__(self) -> None:
        if self.negative_price_rule not in {"none", "six_hour", "new_2025"}:
            raise ValueError(f"unknown negative_price_rule {self.negative_price_rule!r}")


@dataclass(frozen=True)
class RunConfig:
    dt: float = 0.25
    horizon_steps: int = 96
    # Off by default, on evidence. With a receding horizon of 12 h or more and re-solving every
    # step, B3 reached the perfect-foresight ceiling exactly under perfect forecasts (100%)
    # and 88% of the headroom at 15% forecast error. The linear terminal value turned that
    # into -179%: it hoarded 34 to 38 MWh in the battery. See `default_terminal_price`.
    terminal_value: bool = False
    terminal_price: float | None = None
    forecast_sigma: float = 0.15
    forecast_rho: float = 0.8
    seed: int = 0

    plant: PlantConfig = field(default_factory=PlantConfig)
    market: MarketConfig = field(default_factory=MarketConfig)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)

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
    """Co-located utility hybrid plant configuration.

    Note on planned ramp rate constraints:
    - ramp_mw_per_step: declared for planned grid-code compliance extensions;
      defaults to None (inverter-coupled plant operates without active ramp limits).
    """
    wind_mw: float = 50.0
    pv_mw: float = 30.0
    bess_mw: float = 20.0
    bess_mwh: float = 40.0
    conn_mw: float = 60.0            # point-of-interconnection export limit

    soc_min_frac: float = 0.05
    soc_max_frac: float = 0.95
    # None starts the battery at soc_min_frac, i.e. empty, and that is what the ladder needs.
    # No rung has an end-of-window state-of-charge condition, so energy found in the battery
    # at the start is sold as revenue nobody paid for: half full, that was 18 MWh, counted as
    # battery value (B2 minus B0). Starting empty needs no price for stored energy.
    soc_init_frac: float | None = None
    eta_c: float = 0.95
    eta_d: float = 0.95
    c_deg_eur_mwh: float = 3.0       # EUR per MWh of throughput

    # Whether the battery may charge FROM THE GRID or only from co-located generation.
    # This is a regulatory and metering question, not a technical one, and the answer
    # materially changes the business case - hence a switch rather than an assumption.
    grid_charging_allowed: bool = False

    ramp_mw_per_step: float | None = None   # None = unlimited (inverter-coupled plant)

    def __post_init__(self) -> None:
        # A start outside the limits is a state the plant cannot be in, and the safety layer
        # forces the battery back: from below it charged, from the grid even where grid
        # charging is forbidden; from above it sold energy nobody paid for.
        if self.soc_init_frac is not None and not (
                self.soc_min_frac <= self.soc_init_frac <= self.soc_max_frac):
            raise ValueError(f"soc_init_frac {self.soc_init_frac} lies outside [soc_min_frac "
                             f"{self.soc_min_frac}, soc_max_frac {self.soc_max_frac}]")

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
        frac = self.soc_min_frac if self.soc_init_frac is None else self.soc_init_frac
        return self.bess_mwh * frac


# Negative-price rules the settlement implements, oldest vintage first.
NEGATIVE_PRICE_RULES = ("none", "six_hour", "four_hour", "three_hour", "two_hour",
                        "one_hour", "new_2025")


@dataclass(frozen=True)
class MarketConfig:
    """EEG direct marketing, the negative-price rule, and imbalance settlement.

    Note on planned ancillary service extensions:
    The following parameters are declared for planned battery aFRR co-bidding extensions
    and are not yet active in energy-only direct marketing dispatch:
    - afrr_enabled: switch for battery reserve bidding (currently False)
    - afrr_capacity_eur_mw_h: expected reserve capacity clearing price
    - afrr_block_steps: 4-hour reservation product block length (16 quarter-hours)
    """

    # --- EEG market premium ---
    # Revenue = market price + premium, where premium = anzulegender Wert - the technology's
    # national market value of the calendar month, or of the calendar year for plants
    # commissioned from 2023 on and, if tendered, awarded from 2023 on (EEG 2023 Anlage 1
    # Nr. 2; market.market_values, market.premium_table).
    eeg_enabled: bool = True
    # EEG 2023 § 36 onshore wind statutory reference ceiling (source: BNetzA Festlegung 2024/2025)
    anzulegender_wert_wind: float = 73.5     # EUR/MWh
    # EEG 2023 § 37 ground-mounted solar statutory reference ceiling (source: BNetzA Festlegung 2024/2025)
    anzulegender_wert_pv: float = 68.0       # EUR/MWh
    direct_marketing_fee: float = 3.0        # EUR/MWh standard trader management fee (2024/2025)

    # --- negative-price rule (EEG § 51 by vintage: commissioning or tender date, market.py) ---
    # "none"       : commissioned before 2016, premium always paid
    # "six_hour"   : 2016-2020, no premium for the WHOLE run once negative prices last 6 hours
    # "four_hour"  : 2021-2022, the same with 4 hours
    # "three_hour", "two_hour", "one_hour" : the 2023 to 24 Feb 2025 vintage, whose threshold
    #                falls by calendar year (market.vintage_rules)
    # "new_2025"   : from 25 Feb 2025 (Solarspitzengesetz), no premium in ANY negative period
    negative_price_rule: str = "new_2025"

    # --- imbalance ---
    imbalance_enabled: bool = True
    rebap_sigma: float = 55.0                # EUR/MWh, heavy-tailed around the spot price

    # --- optional aFRR participation by the battery (declared for planned extension) ---
    afrr_enabled: bool = False               # declared for planned aFRR extension; not yet active
    afrr_capacity_eur_mw_h: float = 10.0     # declared for planned aFRR extension; not yet active
    afrr_block_steps: int = 16               # declared for planned aFRR extension; not yet active

    # --- redispatch (§ 13a EnWG) ---
    redispatch_prob_per_step: float = 0.001
    redispatch_depth: float = 0.5            # fraction of export capacity ordered away

    def __post_init__(self) -> None:
        if self.negative_price_rule not in NEGATIVE_PRICE_RULES:
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

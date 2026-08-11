"""Configuration for the forecast-to-bid problem.

A balance responsible party holds a renewable portfolio, commits a quarter-hourly position
day-ahead, corrects it intraday as forecasts improve, and settles the residual deviation at
the imbalance price. The decision is sequential under a moving information set, and the cost
of being wrong is strongly asymmetric - which is why the object of study is the DECISION, not
the forecast's RMSE.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class PortfolioConfig:
    wind_mw: float = 200.0
    pv_mw: float = 100.0


@dataclass(frozen=True)
class ForecastConfig:
    """Error growth with lead time, and the ensemble spread that reveals it.

    Forecast error grows with horizon and is strongly autocorrelated. The parameterisation
    below is a stand-in for a real NWP-driven model; what matters for the decision study is
    that (a) errors grow with lead time, (b) they are autocorrelated, and (c) the predictive
    SPREAD is informative about the error magnitude - because that last property is exactly
    what a distributional policy exploits and a point-forecast policy throws away.
    """
    sigma_base: float = 0.05          # relative error at lead time 0
    sigma_growth: float = 0.010       # additional relative error per hour of lead time
    sigma_max: float = 0.30
    rho: float = 0.85                 # autocorrelation of the error process
    n_quantiles: int = 9              # 0.1 ... 0.9
    heteroscedastic: bool = True      # spread varies with the situation, and is observable


@dataclass(frozen=True)
class MarketConfig:
    """Day-ahead, intraday and imbalance mechanics."""

    # --- intraday correction stages, as hours before delivery ---
    id_stages_h: tuple[float, ...] = (12.0, 6.0, 2.0, 0.5)

    # --- liquidity: the cost of correcting a position ---
    # No open order-book data exists, so spread and impact are parameterised and SWEPT. Every
    # result in this project is reported as a function of these numbers rather than at a
    # single assumed point - assuming frictionless trading would flatter every policy and
    # invalidate the comparison.
    id_spread_eur_mwh: float = 3.0            # half-spread paid on any intraday trade
    id_impact_eur_mwh_per_mw: float = 0.02    # linear market impact
    id_spread_widening_near_gate: float = 2.0  # multiplier on the last stage

    # --- imbalance ---
    rebap_sigma: float = 70.0        # EUR/MWh scale of the imbalance price around spot
    rebap_df: float = 3.0            # Student-t degrees of freedom: heavy tails ARE the point
    # Imbalance is signed and can REWARD deviation in the system-helping direction. Modelling
    # it as a symmetric penalty removes the most interesting feature of the problem.
    rebap_sign_correlation: float = 0.4   # correlation between system imbalance and price sign


@dataclass(frozen=True)
class RunConfig:
    dt: float = 0.25
    seed: int = 0
    risk_aversion: float = 0.0       # 0 = expected value; >0 shifts toward CVaR

    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    market: MarketConfig = field(default_factory=MarketConfig)

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt))

    def with_(self, **kwargs) -> "RunConfig":
        return replace(self, **kwargs)

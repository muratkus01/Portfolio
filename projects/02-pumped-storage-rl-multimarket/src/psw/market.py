"""Markets: day-ahead energy, aFRR capacity and activation, imbalance, network charges.

The single settlement function for the project. Every rung of the ladder and the RL reward
recompute revenue through `settle`, so the optimiser's objective and the reported result
cannot drift apart.

Revenue streams, and why each matters:

  energy        day-ahead / intraday value of turbining minus the cost of pumping
  capacity      paid per MW-hour of aFRR held available, whether or not it is called
  activation    energy actually called, at a premium (POS) or discount (NEG) to spot
  imbalance     deviation from the committed schedule, settled at reBAP
  network       s118(6) EnWG exempts pumping from network charges under conditions -
                a switch that materially changes the arbitrage spread
"""
from __future__ import annotations

import numpy as np

from .config import MarketConfig, PlantConfig


# --------------------------------------------------------------------- activation model
def activation_series(n: int, sold_pos: np.ndarray, sold_neg: np.ndarray,
                      cfg: MarketConfig, rng: np.random.Generator) -> np.ndarray:
    """Signed MW of balancing energy actually called, given capacity sold per step.

    Autocorrelated rather than white: aFRR activation comes in episodes driven by system
    imbalance, and treating it as independent noise makes the reservoir risk look far smaller
    than it is. This is the term whose distribution a learned policy is hypothesised to value
    better than a point-forecast MPC (RQ1/RQ2).
    """
    def ar(rate: float) -> np.ndarray:
        z = rng.standard_normal(n)
        for i in range(1, n):
            z[i] = cfg.activation_rho * z[i - 1] + np.sqrt(1 - cfg.activation_rho ** 2) * z[i]
        u = 0.5 * (1 + np.tanh(z))                     # -> (0, 1), autocorrelated
        return np.clip(u * 2 * rate, 0.0, 1.0)          # mean ~= rate

    pos = ar(cfg.afrr_pos_activation_rate) * sold_pos   # upward = more turbining
    neg = ar(cfg.afrr_neg_activation_rate) * sold_neg   # downward = more pumping
    return pos - neg


# --------------------------------------------------------------------- settlement
def settle(res: dict[str, np.ndarray], price: np.ndarray, dt: float,
           plant: PlantConfig, market: MarketConfig,
           sold_pos: np.ndarray | None = None, sold_neg: np.ndarray | None = None,
           activation: np.ndarray | None = None,
           schedule: np.ndarray | None = None,
           rebap: np.ndarray | None = None) -> dict[str, float]:
    """Decompose the operating result. All values EUR over the evaluated period.

    Two legs, never mixed. The COMMERCIAL schedule (`res["p_sched"]`) earns or pays the
    day-ahead price. Activated balancing energy, the physical output minus the schedule, earns
    the aFRR energy price. An earlier version paid day-ahead on the PHYSICAL output, which
    already contains the activation, and then paid activation again on top: every activated
    MWh was settled twice. Network charges and wear follow the physical machine.
    """
    p = res["p"]
    p_p = res["p_pump"]
    sched = np.asarray(res.get("p_sched", p), dtype=float)

    energy_revenue = float(np.sum(np.maximum(sched, 0.0) * price) * dt)
    pump_cost = float(np.sum(np.maximum(-sched, 0.0) * price) * dt)

    network_cost = 0.0
    if not plant.para_118_6_exempt:
        network_cost = float(np.sum(p_p) * dt * plant.network_charge_pump)

    capacity_revenue = 0.0
    if sold_pos is not None and sold_neg is not None:
        capacity_revenue = float(
            np.sum(sold_pos) * dt * market.afrr_pos_capacity_eur_mw_h
            + np.sum(sold_neg) * dt * market.afrr_neg_capacity_eur_mw_h)

    # energy actually delivered for balancing: what the machine did beyond its schedule
    delivered = (p - sched) if "p_sched" in res else activation
    activation_revenue = 0.0
    if delivered is not None:
        up = np.maximum(delivered, 0.0)
        dn = np.maximum(-delivered, 0.0)
        activation_revenue = float(
            np.sum(up * (price + market.afrr_pos_energy_premium)) * dt
            - np.sum(dn * (price - market.afrr_neg_energy_discount)) * dt)

    imbalance_cost = 0.0
    if schedule is not None and rebap is not None:
        dev = p - schedule                       # >0 = delivered more than scheduled
        imbalance_cost = float(-np.sum(dev * rebap) * dt)

    wear = (float(np.sum(res["mode_changes"])) * plant.mode_change_cost)

    net = (energy_revenue - pump_cost - network_cost + capacity_revenue
           + activation_revenue - imbalance_cost - wear)
    return {
        "energy_revenue": energy_revenue,
        "pump_cost": pump_cost,
        "network_cost": network_cost,
        "capacity_revenue": capacity_revenue,
        "activation_revenue": activation_revenue,
        "imbalance_cost": imbalance_cost,
        "wear_cost": wear,
        "net_revenue": net,
    }


def step_revenue(p_sched: float, p_real: float, price: float, dt: float,
                 plant: PlantConfig, market: MarketConfig | None = None,
                 sold_pos: float = 0.0, sold_neg: float = 0.0,
                 mode_changed: bool = False) -> float:
    """Per-step economic result, EUR matching settle() exactly."""
    energy_rev = max(p_sched, 0.0) * price * dt
    pump_cost = max(-p_sched, 0.0) * price * dt
    network_cost = 0.0
    if not plant.para_118_6_exempt:
        network_cost = max(-p_real, 0.0) * dt * plant.network_charge_pump
    cap_rev = 0.0
    act_rev = 0.0
    if market is not None:
        cap_rev = (sold_pos * market.afrr_pos_capacity_eur_mw_h
                   + sold_neg * market.afrr_neg_capacity_eur_mw_h) * dt
        delivered = p_real - p_sched
        up = max(delivered, 0.0)
        dn = max(-delivered, 0.0)
        act_rev = (up * (price + market.afrr_pos_energy_premium)
                   - dn * (price - market.afrr_neg_energy_discount)) * dt
    wear = plant.mode_change_cost if mode_changed else 0.0
    return energy_rev - pump_cost - network_cost + cap_rev + act_rev - wear


# --------------------------------------------------------------------- capacity bidding
def block_index(n: int, block_steps: int) -> np.ndarray:
    """Map each step to its 4-hour balancing product block."""
    return np.arange(n) // block_steps


def expand_blocks(block_values: np.ndarray, n: int, block_steps: int) -> np.ndarray:
    """Expand a per-block bid vector to a per-step array."""
    return np.repeat(block_values, block_steps)[:n]


def rebap_series(n: int, price: np.ndarray, cfg: MarketConfig,
                 rng: np.random.Generator) -> np.ndarray:
    """Synthetic imbalance price with heavy tails.

    The real series comes from netztransparenz.de and is the first thing to wire in. The
    stand-in is deliberately heavy-tailed (Student-t) because the tails, not the mean, are
    what make the problem distributional - a Gaussian stand-in would quietly remove the very
    feature the project is about.
    """
    if not cfg.rebap_enabled:
        return np.zeros(n)
    t = rng.standard_t(df=3, size=n)
    return price + cfg.rebap_sigma * t / np.sqrt(3.0)

"""Physical site model: battery, inverter, grid connection point.

This module is the single source of truth for the site physics. It is imported by the
rule-based baseline, the MILP, the rolling-horizon MPC and the RL environment alike, so no
rung of the benchmark ladder can accidentally be evaluated against different physics.

Sign convention (used consistently throughout the package):
    p_bat > 0   battery DISCHARGES (supplies the site)
    p_bat < 0   battery CHARGES
    net = load - pv - p_bat ;  net > 0 -> import, net < 0 -> export
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import SiteConfig


@dataclass(frozen=True)
class StepResult:
    """Outcome of one dispatch step, in physical units."""
    soc: float          # kWh at the END of the step
    p_bat: float        # kW, applied (post-safety-layer) battery power
    p_ch: float         # kW, charging magnitude
    p_dis: float        # kW, discharging magnitude
    p_imp: float        # kW, grid import
    p_exp: float        # kW, grid export
    throughput: float   # kWh charged + discharged during the step


def split_battery_power(p_bat: float) -> tuple[float, float]:
    """Split a signed battery power into (charge magnitude, discharge magnitude).

    Using a signed action makes charge/discharge mutual exclusion structural rather than a
    constraint that must be enforced - one of the simplifications the RL formulation gains
    over the MILP, which needs two variables and two binaries for the same thing.
    """
    return max(-p_bat, 0.0), max(p_bat, 0.0)


def soc_next(soc: float, p_bat: float, dt: float, cfg: SiteConfig) -> float:
    """Battery energy balance.

    NOTE the `* dt`. The original model omitted it, which is invisible at dt = 1 h and wrong
    by a factor of 4 at dt = 0.25 h. See docs/08-milp-to-rl-roadmap.md, defect D1.
    """
    p_ch, p_dis = split_battery_power(p_bat)
    return soc + (cfg.eta_c * p_ch - p_dis / cfg.eta_d) * dt


def grid_exchange(load: float, pv: float, p_bat: float) -> tuple[float, float]:
    """Resolve the site power balance into (import, export), both non-negative."""
    net = load - pv - p_bat
    return max(net, 0.0), max(-net, 0.0)


def step(soc: float, p_bat: float, load: float, pv: float, dt: float,
         cfg: SiteConfig) -> StepResult:
    """Advance the site one step under an ALREADY FEASIBLE battery power.

    This function does not clip: feasibility is the safety layer's responsibility, and mixing
    the two would hide violations instead of preventing them. Use `prosumer.safety.project`
    first, then call this.
    """
    p_ch, p_dis = split_battery_power(p_bat)
    s1 = soc_next(soc, p_bat, dt, cfg)
    p_imp, p_exp = grid_exchange(load, pv, p_bat)
    return StepResult(
        soc=s1, p_bat=p_bat, p_ch=p_ch, p_dis=p_dis,
        p_imp=p_imp, p_exp=p_exp, throughput=(p_ch + p_dis) * dt,
    )


def wear_energy(p_ch, p_dis, dt: float, cfg: SiteConfig):
    """Energy that the wear price `c_deg` and the daily cap apply to, per `cfg.wear_basis`."""
    return (p_dis * dt) if cfg.wear_basis == "discharge" else (p_ch + p_dis) * dt


def simulate(p_bat: np.ndarray, load: np.ndarray, pv: np.ndarray, dt: float,
             cfg: SiteConfig, soc0: float | None = None) -> dict[str, np.ndarray]:
    """Run a whole dispatch trajectory. Returns arrays aligned with the input.

    `soc` in the result is the state at the END of each step, matching the convention of the
    original model (where SOC[t] was the post-step state).
    """
    n = len(p_bat)
    soc = np.empty(n)
    p_ch = np.empty(n)
    p_dis = np.empty(n)
    p_imp = np.empty(n)
    p_exp = np.empty(n)
    s = cfg.soc_init if soc0 is None else soc0
    for t in range(n):
        r = step(s, float(p_bat[t]), float(load[t]), float(pv[t]), dt, cfg)
        soc[t], p_ch[t], p_dis[t], p_imp[t], p_exp[t] = r.soc, r.p_ch, r.p_dis, r.p_imp, r.p_exp
        s = r.soc
    return {
        "soc": soc, "p_bat": np.asarray(p_bat, dtype=float),
        "p_ch": p_ch, "p_dis": p_dis, "p_imp": p_imp, "p_exp": p_exp,
        "throughput": (p_ch + p_dis) * dt,
        "wear": wear_energy(p_ch, p_dis, dt, cfg),
    }


def check_feasible(res: dict[str, np.ndarray], dt: float, cfg: SiteConfig,
                   tol: float = 1e-6) -> dict[str, int]:
    """Count constraint violations in a simulated trajectory.

    Every rung of the ladder is run through this. The expected result everywhere in this
    project is all-zeros: hard constraints are enforced by construction, not priced into an
    objective. A non-zero count is a bug, not a trade-off.
    """
    return {
        "soc_low": int(np.sum(res["soc"] < cfg.soc_min - tol)),
        "soc_high": int(np.sum(res["soc"] > cfg.soc_max + tol)),
        "inverter": int(np.sum(np.abs(res["p_bat"]) > cfg.p_inv + tol)),
        "import": int(np.sum(res["p_imp"] > cfg.p_imp_max + tol)),
        "export": int(np.sum(res["p_exp"] > cfg.p_exp_max + tol)),
        "simultaneous": int(np.sum((res["p_ch"] > tol) & (res["p_dis"] > tol))),
    }


def energy_balance_error(res: dict[str, np.ndarray], load: np.ndarray, pv: np.ndarray,
                         dt: float, cfg: SiteConfig) -> float:
    """Maximum absolute violation of the site power balance, in kW.

    Property test target: this must be ~0 for any trajectory produced by `simulate`.
    """
    lhs = res["p_imp"] - res["p_exp"]
    rhs = load - pv - res["p_bat"]
    return float(np.max(np.abs(lhs - rhs)))

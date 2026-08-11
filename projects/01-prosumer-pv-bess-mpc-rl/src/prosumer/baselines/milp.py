"""The MILP core, shared by B2 (perfect foresight) and B3 (rolling horizon).

This is the direct descendant of the original `smart_weekly.py` model, with the defects
listed in docs/08-milp-to-rl-roadmap.md fixed:

  D1  every energy balance carries `* dt`, so the model is correct at any resolution
  D2  import and export are priced separately (see market/tariff.py)
  D3  the objective is `settlement.net_cost` written as a linear expression - the same
      quantity the evaluation recomputes from the realised trajectory
  D4  an optional terminal value on stored energy, so a finite horizon does not drain
      the battery at its end
  D6  the inverter is shared, and grid limits are explicit

On binaries. The original model used `S_ch + S_dc <= 1` with big-M links to enforce that the
battery does not charge and discharge simultaneously. With any round-trip efficiency below 1,
simultaneous charge and discharge strictly loses energy (raising both by delta changes SoC by
`(eta_c - 1/eta_d)*delta*dt < 0` while leaving the grid exchange unchanged), so an optimal
solution never does it and the binaries are redundant. `use_binaries=True` keeps them for
verification; the default relaxes them, which is what makes ~35 000 rolling-horizon re-solves
tractable. `verify_no_simultaneous()` checks the relaxation held.
"""
from __future__ import annotations

import numpy as np
import pulp

from ..config import RunConfig, SiteConfig


def solve_window(load: np.ndarray, pv: np.ndarray,
                 price_import: np.ndarray, price_export: np.ndarray,
                 dt: float, cfg: SiteConfig, soc0: float,
                 terminal_price: float = 0.0,
                 use_binaries: bool = False,
                 throughput_cap: float | None = None,
                 steps_per_day: int | None = None,
                 msg: bool = False) -> dict[str, np.ndarray] | None:
    """Optimise one window. Returns trajectory arrays, or None if infeasible.

    `terminal_price` (EUR/kWh) values energy left in the battery at the end of the window.
    `throughput_cap` (kWh per day) reproduces the original model's hard E_max constraint;
    leave None to rely on the degradation price in the objective instead.
    """
    n = len(load)
    m = pulp.LpProblem("prosumer_dispatch", pulp.LpMinimize)

    p_ch = pulp.LpVariable.dicts("p_ch", range(n), lowBound=0, upBound=cfg.p_inv)
    p_dis = pulp.LpVariable.dicts("p_dis", range(n), lowBound=0, upBound=cfg.p_inv)
    soc = pulp.LpVariable.dicts("soc", range(n), lowBound=cfg.soc_min, upBound=cfg.soc_max)
    p_imp = pulp.LpVariable.dicts("p_imp", range(n), lowBound=0, upBound=cfg.p_imp_max)
    p_exp = pulp.LpVariable.dicts("p_exp", range(n), lowBound=0, upBound=cfg.p_exp_max)

    if use_binaries:
        s_ch = pulp.LpVariable.dicts("s_ch", range(n), cat="Binary")
        s_dis = pulp.LpVariable.dicts("s_dis", range(n), cat="Binary")

    # --- objective: net cost, minus the value of energy left in the battery -------------
    m += (
        pulp.lpSum(p_imp[t] * float(price_import[t]) * dt for t in range(n))
        - pulp.lpSum(p_exp[t] * float(price_export[t]) * dt for t in range(n))
        + pulp.lpSum((p_ch[t] + p_dis[t]) * dt * cfg.c_deg for t in range(n))
        - terminal_price * soc[n - 1]
    )

    # --- constraints ---------------------------------------------------------------------
    for t in range(n):
        prev = soc0 if t == 0 else soc[t - 1]
        # D1: the `* dt` that the original model omitted
        m += soc[t] == prev + (cfg.eta_c * p_ch[t] - p_dis[t] / cfg.eta_d) * dt

        # site power balance: import - export == load - pv - (discharge - charge)
        m += (p_imp[t] - p_exp[t]
              == float(load[t]) - float(pv[t]) - (p_dis[t] - p_ch[t]))

        # D6: the inverter is shared between charging and discharging
        m += p_ch[t] + p_dis[t] <= cfg.p_inv

        if use_binaries:
            m += s_ch[t] + s_dis[t] <= 1
            m += p_ch[t] <= cfg.p_inv * s_ch[t]
            m += p_dis[t] <= cfg.p_inv * s_dis[t]

    if throughput_cap is not None and steps_per_day:
        for d0 in range(0, n, steps_per_day):
            d1 = min(d0 + steps_per_day, n)
            m += pulp.lpSum((p_ch[t] + p_dis[t]) * dt for t in range(d0, d1)) <= throughput_cap

    m.solve(pulp.PULP_CBC_CMD(msg=msg))
    if pulp.LpStatus[m.status] != "Optimal":
        return None

    ch = np.array([p_ch[t].value() or 0.0 for t in range(n)])
    dis = np.array([p_dis[t].value() or 0.0 for t in range(n)])
    return {
        "soc": np.array([soc[t].value() for t in range(n)]),
        "p_bat": dis - ch,
        "p_ch": ch,
        "p_dis": dis,
        "p_imp": np.array([p_imp[t].value() or 0.0 for t in range(n)]),
        "p_exp": np.array([p_exp[t].value() or 0.0 for t in range(n)]),
        "throughput": (ch + dis) * dt,
        "objective": np.array([pulp.value(m.objective)]),
    }


def verify_no_simultaneous(res: dict[str, np.ndarray], tol: float = 1e-6) -> int:
    """Count steps where the relaxed model charged and discharged at once. Expected: 0."""
    return int(np.sum((res["p_ch"] > tol) & (res["p_dis"] > tol)))


def default_terminal_price(price_import: np.ndarray, cfg: SiteConfig, run: RunConfig,
                           price_export: np.ndarray | None = None,
                           net_load: np.ndarray | None = None) -> float:
    """Value of a kWh left in the battery at the end of a finite horizon.

    Without a terminal value a rolling-horizon controller empties the battery at every horizon
    end - the artefact that made the original day-by-day model myopic (defect D4, and visible
    in the thesis's own results, where every day closes at minimum SoC).

    Getting the LEVEL right matters as much as having one at all. A stored kWh is worth what
    it will be used for:

      * if the site will IMPORT later, the kWh displaces an import  -> worth `price_import`
      * if the site will EXPORT later, it merely defers an export   -> worth `price_export`

    Valuing it at the import price unconditionally - the obvious first guess - makes the
    controller buy energy from the grid at the end of every horizon in order to bank a value
    it will never realise. On a PV-rich summer week that alone made the rolling-horizon
    controller lose to a price-blind heuristic.

    The estimator below therefore blends the two prices by the share of the horizon in which
    the site is expected to be a net importer, and discounts by the discharge efficiency.
    Fitting a proper terminal value function - regressing B2's shadow price on the SoC
    constraint against state and time features - is the principled upgrade, and is the first
    thing to try if B3 turns out to be horizon-limited.
    """
    if not run.terminal_value:
        return 0.0
    if run.terminal_price is not None:
        return run.terminal_price

    v_imp = float(np.median(price_import))
    if price_export is None or net_load is None:
        return v_imp * cfg.eta_d

    share_import = float(np.mean(np.asarray(net_load) > 0.0))
    v_exp = float(np.median(price_export))
    return (share_import * v_imp + (1.0 - share_import) * v_exp) * cfg.eta_d

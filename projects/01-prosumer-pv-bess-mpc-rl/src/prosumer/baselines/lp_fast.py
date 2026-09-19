"""The relaxed dispatch LP, built as sparse matrices and solved by HiGHS.

Mathematically identical to `milp.solve_window(..., use_binaries=False)` without a daily
throughput cap (asserted in tests/test_rolling_realistic.py), but it skips PuLP's
per-variable Python model building. A 140-step window solves in a few milliseconds instead
of about 30, which is what makes quarter-hourly re-planning over several years affordable.

Variable layout, n steps each: [p_ch | p_dis | soc | p_imp | p_exp].
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix, diags, hstack, identity, vstack

from ..config import SiteConfig


def solve_window_fast(load, pv, price_import, price_export, dt: float, cfg: SiteConfig,
                      soc0: float, terminal_price: float = 0.0) -> dict[str, np.ndarray] | None:
    load, pv, pi, pe = (np.asarray(x, float) for x in (load, pv, price_import, price_export))
    n = len(load)
    I = identity(n, format="csr")
    Z = csr_matrix((n, n))

    # objective
    wear_ch = 0.0 if cfg.wear_basis == "discharge" else cfg.c_deg
    c = np.concatenate([
        np.full(n, wear_ch * dt), np.full(n, cfg.c_deg * dt), np.zeros(n),
        pi * dt, -pe * dt])
    c[2 * n + n - 1] -= terminal_price

    # soc_t - soc_{t-1} - eta_c*dt*ch_t + dt/eta_d*dis_t = 0   (soc_{-1} = soc0)
    shift = diags([np.ones(n - 1)], [-1], shape=(n, n), format="csr")
    a_soc = hstack([-cfg.eta_c * dt * I, (dt / cfg.eta_d) * I, I - shift, Z, Z])
    b_soc = np.zeros(n)
    b_soc[0] = soc0
    # imp - exp - ch + dis = load - pv
    a_bal = hstack([-I, I, Z, I, -I])
    b_bal = load - pv
    # shared inverter: ch + dis <= p_inv
    a_inv = hstack([I, I, Z, Z, Z])

    bounds = ([(0, cfg.p_inv)] * (2 * n) + [(cfg.soc_min, cfg.soc_max)] * n
              + [(0, cfg.p_imp_max)] * n + [(0, cfg.p_exp_max)] * n)
    res = linprog(c, A_ub=a_inv, b_ub=np.full(n, cfg.p_inv),
                  A_eq=vstack([a_soc, a_bal]).tocsr(), b_eq=np.concatenate([b_soc, b_bal]),
                  bounds=bounds, method="highs")
    if res.status != 0:
        return None

    x = res.x
    ch, dis, soc = x[:n], x[n:2 * n], x[2 * n:3 * n]
    return {
        "soc": soc, "p_bat": dis - ch, "p_ch": ch, "p_dis": dis,
        "p_imp": x[3 * n:4 * n], "p_exp": x[4 * n:],
        "throughput": (ch + dis) * dt,
        "wear": (dis if cfg.wear_basis == "discharge" else ch + dis) * dt,
        "objective": np.array([res.fun]),
        # marginal value of one more stored kWh at each step (EUR/kWh)
        "soc_value": -np.asarray(res.eqlin.marginals[:n]),
    }

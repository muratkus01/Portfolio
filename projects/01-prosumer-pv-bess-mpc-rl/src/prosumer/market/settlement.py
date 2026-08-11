"""The single cost function.

In the original script the MILP maximised one expression and the report printed a different
one (see docs/08-milp-to-rl-roadmap.md, defect D3). Here there is exactly one definition of
what a dispatch costs, and it is used by:

  * the MILP objective (B2, B3)          - as a linear expression in the decision variables
  * the evaluation of every rung         - recomputed from the realised trajectory
  * the RL reward                        - as the negative step cost

If the MILP objective value and `settle(...)` on the MILP's own solution ever disagree, that
is a bug, and `tests/test_settlement.py` asserts they do not.
"""
from __future__ import annotations

import numpy as np

from ..config import SiteConfig


def step_cost(p_imp: float, p_exp: float, throughput: float,
              price_import: float, price_export: float, dt: float, c_deg: float) -> float:
    """Cost of one step, EUR. Negative means the step earned money."""
    return (p_imp * price_import * dt
            - p_exp * price_export * dt
            + c_deg * throughput)


def settle(res: dict[str, np.ndarray], price_import: np.ndarray, price_export: np.ndarray,
           dt: float, cfg: SiteConfig) -> dict[str, float]:
    """Total cost of a trajectory, decomposed. All values in EUR over the period."""
    import_cost = float(np.sum(res["p_imp"] * price_import) * dt)
    export_revenue = float(np.sum(res["p_exp"] * price_export) * dt)
    degradation = float(np.sum(res["throughput"]) * cfg.c_deg)
    return {
        "import_cost": import_cost,
        "export_revenue": export_revenue,
        "degradation_cost": degradation,
        "net_cost": import_cost - export_revenue + degradation,
    }


def net_cost(res: dict[str, np.ndarray], price_import: np.ndarray, price_export: np.ndarray,
             dt: float, cfg: SiteConfig) -> float:
    """Scalar objective: total net cost in EUR. Lower is better."""
    return settle(res, price_import, price_export, dt, cfg)["net_cost"]

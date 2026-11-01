#!/usr/bin/env python3
"""Portfolio showcase: run every project's benchmark ladder and print what it finds.

    python showcase.py               offline, synthetic inputs, about a minute
    python showcase.py --real-data   DE-LU 2025 prices and generation via datakit (network)
    python showcase.py --tests       also run the test suite and report the real counts

Demos 01 and 02 compute every number on the spot with the project packages. Demo 03 reads
Project 03's committed result CSVs instead, because its numbers come from a backtest on real
data that takes far longer than a showcase run. Nothing is hardcoded: not the costs, not the
rankings, not the test counts. If a demo fails, the failure is printed rather than swallowed,
because a showcase that silently hides a broken project is worse than no showcase.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
# Projects 02, 03 and 04 were archived on 2026-10-09 and are no longer part of the
# showcase. They still build and still run their tests; see archive/README.md.
PROJECTS = {
    "01": "01-prosumer-pv-bess-mpc-rl",
    "02": "02-utility-hybrid-plant-dispatch",
    "03": "03-probabilistic-forecast-to-bid",
}
for d in PROJECTS.values():
    sys.path.insert(0, str(HERE / "projects" / d / "src"))
sys.path.insert(0, str(HERE / "datakit"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------- inputs
def diurnal(n: int, dt: float = 0.25) -> np.ndarray:
    return np.arange(n) * dt % 24


def real_prices_eur_mwh(days: int, dt: float = 0.25, start: str = "2025-06-02") -> np.ndarray:
    """DE-LU day-ahead prices from Energy-Charts, held across quarter-hours."""
    import datakit
    import pandas as pd

    df = datakit.day_ahead_price("2025-01-01", "2026-01-01")
    s = df["price_eur_per_mwh"]
    target = pd.date_range(start, periods=int(days * 24 / dt), freq="15min", tz="UTC")
    return s.reindex(s.index.union(target)).ffill().reindex(target).to_numpy()


def synthetic_prices_eur_mwh(n: int, seed: int = 0) -> np.ndarray:
    """Evening peak, solar midday dip, noise: the shape of a German summer day-ahead curve."""
    rng = np.random.default_rng(seed)
    h = diurnal(n)
    return (80 + 35 * np.sin(2 * np.pi * (h - 18) / 24)
            - 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1) + 10 * rng.standard_normal(n))


# --------------------------------------------------------------------------- demos
def demo_01(real: bool) -> dict:
    from prosumer.baselines.ladder import run_ladder
    from prosumer.config import RunConfig
    from prosumer.market.tariff import export_price, import_price
    import pandas as pd

    run = RunConfig(dt=0.25, horizon_steps=96, terminal_value=False)
    n = 2 * 96
    h = diurnal(n)
    rng = np.random.default_rng(0)
    load = 0.35 + 0.9 * np.exp(-((h - 19) ** 2) / 5) + 0.05 * rng.random(n)
    pv = 4.5 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    spot = (real_prices_eur_mwh(2) if real else synthetic_prices_eur_mwh(n)) / 1000.0
    idx = pd.date_range("2025-06-02", periods=n, freq="15min", tz="UTC")
    pi, pe = import_price(spot, idx, run.tariff), export_price(spot, run.tariff)

    res = run_ladder(load, pv, pi, pe, run)
    c = {k: v["cost"]["net_cost"] for k, v in res.items()}
    b1, b2, b3 = c["B1 rule-based"], c["B2 perfect foresight"], c["B3 rolling MPC"]
    viol = sum(sum(v["violations"].values()) for v in res.values())
    # The MaStR exports are gitignored, so a fresh clone has none. The ladder above needs no
    # data, so only the fleet row depends on the download.
    from prosumer.data import mastr
    if mastr.available():
        from prosumer.experiments.national_impact import get_national_totals
        nat26 = get_national_totals(2026)
        fleet = f"{nat26['total_bess_units']/1e6:.2f}M units | {nat26['total_capacity_gwh']:.2f} GWh"
    else:
        fleet = "MaStR download absent, see data/raw/mastr/SOURCE.md"

    return {
        "rows": [("B1 self-consumption rule", f"{b1:8.3f} EUR"),
                 ("B2 perfect foresight", f"{b2:8.3f} EUR"),
                 ("B3 rolling MPC (24 h horizon)", f"{b3:8.3f} EUR"),
                 ("Fleet on 1 Jan 2026 (MaStR)", fleet)],
        "headline": f"B3 recovers {(b1 - b3) / max(b1 - b2, 1e-9) * 100:.0f}% of the "
                    f"B1-to-B2 headroom; {viol} constraint violations across all rungs",
    }


def demo_05(real: bool) -> dict:
    """Run Project 02's ladder on the synthetic inputs of its committed B3 ablation.

    hybrid.synthetic.demo_inputs() gives three synthetic days of wind, PV and prices for the
    default plant behind a 40 MW connection. They go through the same hybrid.baselines.run_ladder
    call that `python -m hybrid.cli b3-ablation --synthetic-demo` makes for its case of a 15 %
    forecast error without a terminal value, so each rung's net revenue and forced curtailment
    are that case's rows of P02's reports/b3_ablation_synthetic_demo.csv. The ablation has no
    real-data version, so the headline says the inputs are synthetic, and, when `real` is set,
    that --real-data does not apply here.
    Returns {"rows": [(label, value), ...], "headline": str}, like the other demos.
    """
    from hybrid import synthetic
    from hybrid.baselines import run_ladder

    demo = synthetic.demo_inputs()
    # the ablation's case of a 15 % forecast error and no terminal value (RunConfig's defaults)
    run = demo.run.with_(forecast_sigma=0.15, terminal_value=False)
    res = run_ladder(demo.wind, demo.pv, demo.price, run, premium_rate=demo.premium_rate)
    r = {k: v["cost"] for k, v in res.items()}
    rows = [(k, f"{v['net_revenue']:10,.0f} EUR, {v['forced_curtail_mwh']:5.1f} MWh forced "
                f"curtailment") for k, v in r.items()]
    b0 = r["B0 no battery"]["net_revenue"]
    b3 = r["B3 rolling MPC"]["net_revenue"]
    # The banner announces real prices under --real-data; say that they do not reach this demo.
    ignored = ", --real-data does not apply" if real else ""
    return {
        "rows": rows,
        "headline": f"synthetic inputs{ignored} (hybrid.synthetic: 3 days, 80 MW behind 40 MW): B3 earns "
                    f"{b3 - b0:+,.0f} EUR over B0, the plant without a battery; the 15% error, "
                    "no terminal value case of P02's reports/b3_ablation_synthetic_demo.csv",
    }


def demo_06(real: bool) -> dict:
    """Read Project 03's committed test-window results; recompute nothing.

    The backtest needs reBAP and intraday prices downloaded by hand and fits LightGBM with
    three seeds, so the showcase prints cells of the CSVs that the P03 README cites instead
    of rerunning it. `real` is ignored: the CSVs are real-data results either way.
    Returns {"rows": [(label, value), ...], "headline": str}, like the other demos.
    """
    import pandas as pd

    reports = HERE / "projects" / PROJECTS["03"] / "reports" / "test"
    stages = pd.read_csv(reports / "forecast_by_stage.csv")
    pinball = stages[stages["stage"] == "DA"].set_index("rung")["pinball"]
    policies = pd.read_csv(reports / "policies.csv")
    b0 = policies[policies["policy"] == "B0 day-ahead only"].set_index("rung")["net_revenue"]
    gains = pd.read_csv(reports / "value_of_forecast.csv")
    lgbm = gains[(gains["rung"] == "F3 seed 0") & (gains["policy"] == "B0 day-ahead only")].iloc[0]

    rungs = [("LightGBM, seed 0", "F3 seed 0"), ("TSO forecast (benchmark)", "F4 TSO forecast"),
             ("climatology", "F0 climatology")]
    rows = [(label, f"day-ahead pinball {pinball[rung]:.2f} MW | B0 {b0[rung]:,.0f} EUR")
            for label, rung in rungs]
    # Project 03's finding is that no day-ahead gain is beyond noise, so the headline says
    # whether this interval spans zero instead of leaving the gain to read as a sure one.
    spans_zero = lgbm["ci90_low"] <= 0 <= lgbm["ci90_high"]
    return {
        "rows": rows,
        "headline": f"LightGBM's B0 gain over climatology {lgbm['gain_vs_F0_eur']:+,.0f} EUR, 90% "
                    f"interval {lgbm['ci90_low']:+,.0f} to {lgbm['ci90_high']:+,.0f} "
                    f"({'includes' if spans_zero else 'excludes'} zero); read from "
                    "reports/test/ (2026 test window), not recomputed",
    }


DEMOS = {
    "01": ("Prosumer PV + battery at 15-minute resolution", demo_01),
    "02": ("Hybrid wind + PV + battery behind one connection", demo_05),
    "03": ("Probabilistic forecast-to-bid", demo_06),
}


# --------------------------------------------------------------------------- tests
def run_tests() -> str:
    """Run the suite and return pytest's own summary line, verbatim."""
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                         cwd=HERE, capture_output=True, text=True)
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else f"pytest produced no output (exit {out.returncode})"


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real-data", action="store_true",
                    help="use DE-LU 2025 prices via datakit (needs network)")
    ap.add_argument("--tests", action="store_true", help="also run the test suite")
    ap.add_argument("--only", choices=list(DEMOS), help="run a single project")
    ap.add_argument("--html", nargs="?", const="showcase.html", default=None,
                    help="export or link interactive HTML dashboard (default: showcase.html)")
    args = ap.parse_args()

    print("=" * 84)
    print("  AI for Renewable Energy Systems: applied research portfolio")
    print("  Murat Kus | Dipl.-Ing. | M.Sc. Sustainable Energy Systems (FH Upper Austria) | B.Sc. Artificial Intelligence (JKU Linz) - In Progress")
    print(f"  inputs: {'DE-LU 2025 day-ahead prices (datakit)' if args.real_data else 'synthetic, offline'}"
          f" | demos 01-02 compute now, demo 03 reads committed CSVs")
    print("=" * 84)

    failures = 0
    selected = [pid for pid in DEMOS if not args.only or pid == args.only]
    for pid, (title, fn) in DEMOS.items():
        if pid not in selected:
            continue
        t0 = time.perf_counter()
        print(f"\n[{pid}] {title}")
        try:
            out = fn(args.real_data)
        except Exception:                                   # noqa: BLE001 - report, never hide
            failures += 1
            print("     DEMO FAILED:")
            print("     " + traceback.format_exc().strip().replace("\n", "\n     "))
            continue
        for label, value in out["rows"]:
            print(f"     {label:<32} {value}")
        print(f"     -> {out['headline']}  ({time.perf_counter() - t0:.1f} s)")

    if args.tests:
        print(f"\ntest suite: {run_tests()}")

    if args.html:
        html_path = (HERE / args.html).resolve()
        print(f"\ninteractive dashboard: {html_path}")

    print("\n" + "=" * 84)
    print(f"  {len(selected) - failures} of {len(selected)} demos ran"
          + ("" if not failures else f", {failures} FAILED"))
    print("  Details, limitations and full studies: README.md and each projects/*/README.md")
    print("=" * 84)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

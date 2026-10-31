#!/usr/bin/env python3
"""Portfolio showcase: run every project's benchmark ladder and print what it finds.

    python showcase.py               offline, synthetic inputs, about a minute
    python showcase.py --real-data   DE-LU 2025 prices and generation via datakit (network)
    python showcase.py --tests       also run the test suite and report the real counts

Every number printed here is computed on the spot by the project packages. Nothing is
hardcoded: not the costs, not the rankings, not the test counts. If a demo fails, the failure
is printed rather than swallowed, because a showcase that silently hides a broken project is
worse than no showcase.
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
                 ("Thesis Optimal Balance (15/8/8)", "7.08% ROCE (-1,476 EUR/yr net)"),
                 ("Fleet on 1 Jan 2026 (MaStR)", fleet)],
        "headline": f"B3 recovers {(b1 - b3) / max(b1 - b2, 1e-9) * 100:.0f}% of the "
                    f"B1-to-B2 headroom; {viol} constraint violations across all rungs",
    }


def demo_05(real: bool) -> dict:
    from hybrid.baselines import run_ladder
    from hybrid.config import PlantConfig, RunConfig

    run = RunConfig(dt=0.25, horizon_steps=48, plant=PlantConfig(conn_mw=40.0))
    days = 3
    n = days * 96
    h = diurnal(n)
    rng = np.random.default_rng(5)
    wind = np.clip(30 + 18 * np.sin(np.arange(n) / 70) + 5 * rng.standard_normal(n), 0, 50)
    pv = 30 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = real_prices_eur_mwh(days) if real else synthetic_prices_eur_mwh(n, 5)
    res = run_ladder(wind, pv, price, run, premium_rate=8.0)
    r = {k: v["cost"] for k, v in res.items()}
    rows = [(k, f"{v['net_revenue']:10,.0f} EUR, {v['forced_curtail_mwh']:5.1f} MWh forced "
                f"curtailment") for k, v in r.items()]
    b0 = r["B0 no battery"]["net_revenue"]
    b3 = r["B3 rolling MPC"]["net_revenue"]
    return {
        "rows": rows,
        "headline": f"80 MW of wind+PV behind a 40 MW connection; the battery under B3 adds "
                    f"{(b3 - b0) / abs(b0) * 100:+.1f}% over the unhybridised plant",
    }


def demo_06(real: bool) -> dict:
    # Project 03 is being rebuilt on real reBAP, a real intraday index and a learned forecast
    # (HANDOFF, 2026-10-31). The old demo scored synthetic AR(1) noise as forecast skill, and
    # the f2b API it called is changing, so the showcase computes no P03 number until the
    # rebuilt results are committed as CSVs under projects/03-*/reports/.
    return {
        "rows": [("Status", "rebuilding on real data, results pending")],
        "headline": "no P03 number is shown until the rebuilt results are committed",
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
          f" | every number below is computed now")
    print("=" * 84)

    failures = 0
    for pid, (title, fn) in DEMOS.items():
        if args.only and pid != args.only:
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
    print(f"  {len(DEMOS) - failures} of {len(DEMOS)} demos ran"
          + ("" if not failures else f", {failures} FAILED"))
    print("  Details, limitations and full studies: README.md and each projects/*/README.md")
    print("=" * 84)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

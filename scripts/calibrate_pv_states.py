"""Calibrate and validate the per-state 1 kWp PV model against the reference year.

Answers one question: are public Open-Meteo irradiance data good enough to stand in for the
company reference export? The annual yield is NOT evidence, because the single loss scalar is
fitted to it. The evidence is everything else: daily correlation, hourly error, peaks, the
worst month, and the hour of day the generation is centred on.

Writes the fitted losses to a gitignored file. The reference exports and anything derived
from them stay out of the repository.

    python scripts/calibrate_pv_states.py            # all states, 2024
    python scripts/calibrate_pv_states.py BY BE SN   # a subset
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "projects" / "01-prosumer-pv-bess-mpc-rl" / "src"))
sys.path.insert(0, str(ROOT / "datakit"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from prosumer.data import pv_states as ps  # noqa: E402

YEAR = 2024


def main(codes: list[str]) -> int:
    refs = ps.find_reference_files()
    if not refs:
        print(f"No reference files in {ps.reference_dir()}")
        print("Copy the per-state 1 kWp exports there. The folder is gitignored on purpose.")
        return 1

    todo = [c for c in (codes or sorted(ps.STATE_SITES)) if c in refs]
    skipped = [c for c in (codes or sorted(ps.STATE_SITES)) if c not in refs]
    if skipped:
        print(f"no reference file for {', '.join(skipped)}, skipping those")

    rows, losses = [], {}
    for i, code in enumerate(todo, 1):
        site = ps.STATE_SITES[code]
        print(f"[{i}/{len(todo)}] {code} {site.name} ... ", end="", flush=True)
        try:
            ref = ps.load_reference_1kwp(refs[code], YEAR)
            loss = ps.fit_system_loss(code, ref, YEAR)
            report = ps.validate(code, ref, loss, YEAR)
        except Exception as exc:                                   # noqa: BLE001
            print(f"FAILED: {type(exc).__name__}: {exc}")
            continue
        rows.append(report)
        losses[code] = {"system_loss": loss, "reference_file": refs[code].name,
                        "reference_year": YEAR}
        print(f"loss {loss * 100:5.2f} %  daily R2 {report['daily_r2']:.3f}  "
              f"hourly nRMSE {report['hourly_nrmse']:.3f}  "
              f"offset {report['centroid_offset_hours']:+.2f} h")

    if not rows:
        print("nothing calibrated")
        return 1

    df = pd.DataFrame(rows).set_index("state")
    cols = ["system_loss", "yield_reference", "yield_model", "daily_r2", "daily_nrmse",
            "hourly_nrmse", "hourly_bias_pct", "peak_ratio", "worst_month_pct",
            "centroid_offset_hours"]
    print("\n" + df[cols].to_string())
    print("\nmedians: " + ", ".join(
        f"{c} {df[c].median():.4g}" for c in ("daily_r2", "hourly_nrmse", "peak_ratio",
                                              "worst_month_pct", "centroid_offset_hours")))

    out = ps.calibration_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "note": "Fitted against a company reference export. Local only, never commit.",
        "year": YEAR,
        "weather_source": "Open-Meteo historical-forecast archive, 15-minute, CC BY 4.0",
        "states": losses,
        "validation": {r["state"]: r for r in rows},
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    print("gitignored:", "yes" if "/data/" in out.as_posix() else "CHECK THIS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main([a.upper() for a in sys.argv[1:]]))

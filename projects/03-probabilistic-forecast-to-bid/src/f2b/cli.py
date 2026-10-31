"""Command-line entry point for the forecast-to-bid project.

Real data, the source of every README result:
    python -m f2b.cli build-data                    fetch and cache every input
    python -m f2b.cli backtest --phase validation   fit the ladder, tune on validation
    python -m f2b.cli backtest --phase test         evaluate the 2026 window, once

Parametric noise ablation, kept for comparison only:
    python -m f2b.cli synthetic-policies       [--year 2025 --days 60 --spread 3 --seed 0]
    python -m f2b.cli synthetic-forecast       forecast scores by decision stage
    python -m f2b.cli synthetic-score-vs-value does the forecast score rank forecasts by EUR?
    python -m f2b.cli synthetic-liquidity      sensitivity to the intraday spread
"""
from __future__ import annotations

import argparse

from . import ablation, pipeline


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="f2b", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-data").set_defaults(func=pipeline.cmd_build_data)
    sp = sub.add_parser("backtest")
    sp.add_argument("--phase", choices=("validation", "test"), required=True)
    sp.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    sp.add_argument("--spread", type=float, default=3.0)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=pipeline.cmd_backtest)
    for name, fn in ablation.COMMANDS.items():
        sp = sub.add_parser(name)
        ablation.add_arguments(sp)
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

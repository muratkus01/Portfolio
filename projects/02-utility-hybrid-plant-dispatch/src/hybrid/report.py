"""The committed results: each table the README quotes, as a CSV in reports/, with what it
was computed from.

    python -m hybrid.cli report                        the publish set, one after another
    python -m hybrid.cli report --only sizing_2024     a part of it; processes running other
                                                       parts can work side by side

Each experiment is a command of `hybrid.cli` from the pinned PUBLISH_SET, run with --out
reports/<command>_<window>.csv. Two files beside the tables gather what they rest on:

    window_stats.csv  per window: prices, generation and whether the connection binds
    run_meta.csv      per experiment: command, commit, environment, input data, wall time

Each experiment replaces its own rows there under a lock, so parts run apart add up to one
file, and rerunning an experiment updates its rows only.
"""
from __future__ import annotations

import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

from . import cli, data, synthetic, tables
from .config import RunConfig
from .market import market_values
from .provenance import environment, frame_sha256, git_state

# The project folder, found from this file rather than the working directory, so that the
# tables land in the project's reports/ wherever the report is started.
PROJECT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_DIR / "reports"

# The experiments behind the README, as hybrid.cli command lines: B0 to B2 and "B2 no battery"
# over five connections, the negative-price rule by vintage, and the battery's value split
# into arbitrage and the connection limit's effect, each over the calendar years 2024 and
# 2025; B3 at the default plant on 30-day windows from 1 January and 1 July 2025; and the B3
# ablation on the showcase's synthetic inputs.
PUBLISH_SET = (
    "sizing --year 2024 --full-year",
    "sizing --year 2025 --full-year",
    "negprice --year 2024 --full-year",
    "negprice --year 2025 --full-year",
    "decompose --year 2024 --full-year",
    "decompose --year 2025 --full-year",
    "ladder --year 2025 --start 01-01 --days 30",
    "ladder --year 2025 --start 07-01 --days 30",
    "b3-ablation --synthetic-demo",
)


def experiments(commands: tuple[str, ...] | None = None) -> dict[str, str]:
    """Name each command line after its command and window, e.g. "ladder_2025-07-01_30d".

    commands: hybrid.cli command lines, PUBLISH_SET by default.
    Returns {experiment name: command line}, in the given order.
    """
    parser = cli.build_parser()
    named = {}
    for command in PUBLISH_SET if commands is None else commands:
        args = parser.parse_args(shlex.split(command))
        named[f"{args.cmd.replace('-', '_')}_{cli.window_label(args)}"] = command
    return named


def cmd_report(args) -> None:
    """Run the publish set, or the experiments --only names, into the reports folder.

    args: parsed arguments of `hybrid.cli report`: only (names or None), reports_dir (a path
    or None for REPORTS_DIR) and argv, the command line it was started with.
    Raises ValueError naming any experiment --only asks for that the set does not hold.
    """
    reports = Path(args.reports_dir) if args.reports_dir else REPORTS_DIR
    plan = experiments()
    unknown = sorted(set(args.only or ()) - set(plan))
    if unknown:
        raise ValueError(f"no experiment {unknown} in the publish set: {list(plan)}")
    # read before anything is written, and outside the reports folder, so that the tables
    # this run writes do not count as changes to the code that wrote them
    code = git_state(PROJECT_DIR, reports)
    env = environment()
    for name, command in plan.items():
        if not args.only or name in args.only:
            run_experiment(name, command, reports, args.argv, code, env)


def run_experiment(name: str, command: str, reports: Path, argv: list[str], code: dict,
                   env: dict) -> None:
    """Run one experiment into reports/<name>.csv, then put its rows into window_stats.csv and
    run_meta.csv.

    name, command: the experiment and its hybrid.cli command line, as `experiments` gives.
    reports: the folder to write. argv: the report's own command line.
    code, env: `git_state(...)` and `environment()`, read once per report.
    """
    args = cli.build_parser().parse_args([*shlex.split(command),
                                          "--out", str(reports / f"{name}.csv")])
    args.inputs = {}              # the commands note each frame they load here
    print(f"\n=== {name}: python -m hybrid.cli {command}", flush=True)
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    args.func(args)
    run = {"command": command, "argv": shlex.join(argv),
           "started_utc": started.isoformat(timespec="seconds"),
           "wall_time_s": round(time.perf_counter() - t0, 1)}
    meta = [{"experiment": name, "section": section, "item": item, "value": value}
            for section, item, value in _meta(run, code, env, args.inputs)]
    tables.merge_csv(reports / "run_meta.csv", meta, "experiment")
    tables.merge_csv(reports / "window_stats.csv", [_window_stats(args)], "window")


def _meta(run: dict, code: dict, env: dict, inputs: dict) -> list[tuple]:
    """run_meta's rows for one experiment, as (section, item, value).

    run, code, env: what ran, from which commit, with which versions.
    inputs: the frames the experiment loaded, {label: {"year", "days", "frame"}}.
    Returns the rows: run, code and environment; the SHA-256 of each input frame; the
    Energy-Charts requests behind each year loaded; the installed capacity the factors were
    divided by, per month; and the market-value proxy of each month and year loaded whole.
    """
    rows = [("run", key, value) for key, value in run.items()]
    rows += [("code", key, value) for key, value in code.items()]
    rows += [("environment", key, value) for key, value in env.items()]
    rows += [("input_sha256", label, frame_sha256(rec["frame"])) for label, rec in inputs.items()]
    years = sorted({rec["year"] for rec in inputs.values() if rec["year"] is not None})
    rows += [("source_url", f"{year} {dataset}", url) for year in years
             for dataset, url in data.source_urls(year).items()]
    months = {}
    for rec in inputs.values():
        if rec["year"] is not None:
            months.update(data.monthly_capacity(rec["frame"].index).to_dict("index"))
    rows += [("capacity_mw", f"{month} {name}", float(cap[column]))
             for month, cap in sorted(months.items())
             for name, column in (("wind_onshore", "wind_mw"), ("solar_dc", "pv_mw"))]
    for rec in inputs.values():
        if rec["year"] is not None and rec["days"] is None:
            weights = data.market_value_weights(rec["frame"])
            for period in ("month", "year"):
                values = market_values(rec["frame"]["price_eur_mwh"], weights, period)
                rows += [("market_value_proxy_eur_mwh", f"{key} {tech}", float(row[tech]))
                         for key, row in values.iterrows() for tech in ("wind", "pv")]
    return rows


def _window_stats(args) -> dict:
    """window_stats.csv's row for an experiment's window: for the default plant on
    Energy-Charts data, for the demo's own plant on the showcase's synthetic inputs."""
    label = cli.window_label(args)
    if getattr(args, "synthetic_demo", False):
        demo = synthetic.demo_inputs()
        window = tables.window_columns("synthetic (showcase demo_05)", None, len(demo.price),
                                       demo.run.dt)
        return tables.window_stats(label, demo.wind, demo.pv, demo.price, demo.run, window)
    run = RunConfig()
    frame = cli.load_window(args, run.dt)
    wind, pv, price = data.to_plant(frame, run.plant.wind_mw, run.plant.pv_mw)
    window = tables.window_columns("Energy-Charts", args.year, len(frame), run.dt, frame.index)
    return tables.window_stats(label, wind, pv, price, run, window)

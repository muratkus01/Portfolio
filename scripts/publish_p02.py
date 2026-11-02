"""Publish Project 02's tables at one commit: rerun the whole publish set, check that it
reproduces every committed table, add the new tables with their README rows, and commit.

Why: run_meta.csv records the commit each experiment ran from, so tables computed on different
days name different commits, and a table computed in a dirty tree names no commit that holds
its code. This reruns hybrid.report.PUBLISH_SET on one machine, at one pushed commit, in one
step, so that every table names the same reachable commit. A run that does not reproduce what
is committed publishes nothing: the code, the environment or Energy-Charts' data has changed,
and a person must look before any table is replaced.

    python scripts/publish_p02.py                   the whole set, as many parts as CPUs
    python scripts/publish_p02.py --workers 6       at most 6 parts side by side
    python scripts/publish_p02.py --preflight-only  every check before the run, in seconds
    python scripts/publish_p02.py --only decompose_2024 --no-remote-check
                                                    a check of one experiment, publishes nothing

Run it in a clean checkout of --branch (default p02-afrr) at that branch's tip on the remote.
It prints a short summary and exits with 1 at the first failure, with reports/ as committed:

1. Preflight. A clean working tree, also as git sees it when the parts record it; HEAD on
   --branch and at its tip on the remote, so that the commit the tables record is pushed;
   Python 3.12, exactly PINS and pytest; numpy on the code path and PuLP calling the CBC
   binary of the machine the committed tables come from (an x86-64 CPU with AVX2, Windows);
   hybrid and datakit imported from this checkout; no Obsidian vault whose Obsidian-Git
   commits or pulls on its own holds the clone; Energy-Charts and regelleistung.net answer;
   README rows for every new experiment (below). Then every input the set reads is loaded
   once, one after another, so that parts running side by side never download one file at the
   same time: each Energy-Charts frame run_meta.csv records must hash as recorded, or the run
   could not reproduce the committed tables and stops here, in seconds rather than after the
   run; regelleistung.net's aFRR files are checked against their pins by their loader.
   --preflight-only stops after this step.
2. Run. The experiments of PUBLISH_SET, read when the driver runs, in parts side by side: one
   `python -m hybrid.cli report --only ...` process per part, writing into a new staging folder
   in the temp folder, with one log per part. Parts are packed longest first, each experiment
   into the part with the least work so far, as run_meta.csv's wall times estimate it, else
   ESTIMATED_MINUTES, else the longest recorded time, so the longest experiments get parts of
   their own and the cheaper ones share the others. hybrid.report runs a part's experiments in
   PUBLISH_SET's order. A part that fails stops the others.
3. Check. Each fresh experiment records HEAD and git_dirty False. Each committed table equals
   its fresh one cell for cell, as text, apart from TIMING_COLUMNS; window_stats.csv row for
   row; run_meta.csv apart from RUN_META_EXCLUDED. Differences are printed (file, column, first
   rows) and stop the run.
4. New tables, in the set but not committed, take their README rows from --template. A run of
   the whole set always goes on to the install, new tables or not, because its point is that
   run_meta.csv then records one commit for every table. A check with --only installs nothing:
   it ends with "nothing new" where it found nothing new.
5. Install. The fresh tables, run_meta.csv and window_stats.csv go into reports/, the
   template's rows into README.md and reports/SOURCE.md; the P02 suite must pass, or every file
   goes back to its committed state; then one commit, and the push command is printed. The
   driver never pushes.

The template (default scripts/publish_p02_readme_rows.md) is markdown, read only when the run
adds a table or a window. Each line that starts with "|" is a table row, added to the table its
"## " heading names; every other line is a note. The headings and the rows they take:

    ## How each table is made
        README.md's table of that caption: one row per new experiment, as the README tests
        read it, with the experiment's own table first and any other table it writes after
        ", " in the first cell, e.g. for an experiment x of the command x --full-year:
        | [`x.csv`](reports/x.csv) | calendar year 2025 | `python -m hybrid.cli x --full-year` |
    ## Windows of the tables
        README.md's table of that caption: one row per window that window_stats.csv does not
        hold yet, printed as that table prints the others; no row for a known window.
    ## Files
        reports/SOURCE.md's file table: one row per new table or group of tables, which goes in
        before the row of window_stats.csv; "{rows}" in a row becomes the number of rows of its
        tables. The rows of window_stats.csv and run_meta.csv there get their new counts.

README rows go to the end of their table, in the template's order. The preflight refuses a
template that lacks the row of a new experiment, names a table that is committed already or
gives another command than PUBLISH_SET's; the P02 suite then checks every cell against the
fresh tables.
"""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P02 = ROOT / "projects" / "02-utility-hybrid-plant-dispatch"
REPORTS = P02 / "reports"
README = P02 / "README.md"
SOURCE = REPORTS / "SOURCE.md"
TEMPLATE = ROOT / "scripts" / "publish_p02_readme_rows.md"
# Every experiment adds its rows to these two, by experiment and by window; they are not
# tables of their own.
SUPPORT = {"run_meta.csv": "experiment", "window_stats.csv": "window"}
# The staging folder's name, given to the parts relative to their working folder, so that the
# argv rows of run_meta.csv name no local path.
STAGED = "staged_reports"
# This checkout's code first on the path, for the driver and the parts alike, so that the
# publish set that runs, and the commit its tables record, are this checkout's.
CHECKOUT_PATH = [str(P02 / "src"), str(ROOT / "datakit")]
sys.path[:0] = CHECKOUT_PATH

# The interpreter and versions the committed tables were computed with: run_meta.csv's
# environment section records Python, numpy, pandas and PuLP; pyarrow, which backs pandas'
# strings, was installed with them. Another version can move a last digit, which the
# reproduction check would then refuse after the whole run.
PYTHON = (3, 12)
PINS = {"numpy": "2.5.4", "pandas": "3.1.0", "pulp": "3.3.0", "pyarrow": "26.0.0"}
# numpy's code path for float64 sin, which synthetic.py's demo inputs go through, on the
# machine that computed the committed tables: X86_V3, an x86-64 CPU with AVX2 and FMA3. The
# numpy pinned has no higher one, so any such CPU computes the same digits; an ARM CPU, or
# one without AVX2, takes another path.
NUMPY_PATH = "X86_V3"
# The hosts the experiments download from: datakit's Energy-Charts API, and the TSOs' platform
# the aFRR experiments read. An answer with any HTTP status counts as reachable.
HOSTS = ("https://api.energy-charts.info/", "https://www.regelleistung.net/")
# The portfolio's public identity for its commits, whatever this machine's git config holds.
IDENTITY = ("-c", "user.name=muratkus01", "-c", "user.email=muratkus01@hotmail.com")
# The default subjects: of a run that adds tables, and of one that only reruns the committed.
SUBJECT_NEW = "feat(p02): publish the aFRR ablation and rerun every table at one commit"
SUBJECT_RERUN = "chore(p02): rerun every table of the publish set at one commit"
# Minutes of experiments that run_meta.csv records no wall time for yet, so that the packing
# gives the long ones parts of their own. These are aFRR Phase A's estimates (its design,
# section 7.3: on the 4-core work laptop, four parts side by side, as run_meta's wall times
# were measured); a name that is not in the set is ignored, and a recorded wall time wins.
ESTIMATED_MINUTES = {
    "afrr_2024-01-01_30d": 77, "afrr_2024-07-01_30d": 77,
    "afrr_2025-01-01_30d": 77, "afrr_2025-07-01_30d": 77,
    "afrr_2024_fast": 5, "afrr_2025_fast": 5,
    "afrr_2024_fast_up-hours-1": 5, "afrr_2025_fast_up-hours-1": 5,
    "afrr_2025_fast_basis-average": 5, "afrr_2025_fast_basis-marginal": 5,
}
# The Energy-Charts loads run_meta.csv records, as hybrid.cli labels them, e.g.
# "load_year(2025, days=30, dt=0.25)"; the driver repeats each to check its hash.
LOAD_CALL = re.compile(r"load_year\((\d{4}), days=(None|\d+), dt=([0-9.]+)\)")

# Columns that measure the machine, not the plant: a rerun on another machine, or with other
# parts sharing its cores, changes them while every result stays the same. The check leaves
# them out, and the install replaces them with the fresh values. A new column of this kind,
# a retrieval time for instance, makes the check refuse until it is listed here.
TIMING_COLUMNS = {
    "b3_solve_time_mean_s",     # B3's mean CBC solve per rolling step, s of wall time
    "b3_solve_time_max_s",      # B3's longest one
}
# run_meta.csv rows the check leaves out, as (section, item); item None leaves out the section.
RUN_META_EXCLUDED = {
    ("run", "argv"),            # the part's command line: which experiments shared a process
    ("run", "started_utc"),     # when the experiment started
    ("run", "wall_time_s"),     # how long it took on this machine, under this load
    ("code", None),             # git_commit and git_dirty: a rerun records its own, by design
    ("environment", None),      # versions: pinned by the preflight; Python's patch may differ
}
# The tables a template adds rows to, by its headings: (file, the caption the table follows),
# the README's captions as tests/test_readme_results.py finds its tables by.
SECTIONS = {
    "How each table is made": (README, "How each table is made"),
    "Windows of the tables": (README, "Windows of the tables"),
    "Files": (SOURCE, "## Files"),
}


class Refusal(Exception):
    """A check failed: the driver stops, and reports/ stays as committed."""


class Summary:
    """The lines the driver ends with, one per check and step, short enough to paste."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def add(self, label: str, text: str, show: bool = True) -> None:
        """Note one line, e.g. add("git", "a802748 on p02-afrr, clean"), and print it now
        unless show is False."""
        self.lines.append((label, text))
        if show:
            print(_line(label, text), flush=True)

    def print(self) -> None:
        """Print every line noted, under one heading."""
        print("\n== publish_p02 summary ==")
        for label, text in self.lines:
            print(_line(label, text))


def _line(label: str, text: str) -> str:
    """One summary line; the lines of a long text follow, indented."""
    return f"  {label:<10} " + text.replace("\n", "\n" + " " * 13)


def _count(number: int, noun: str) -> str:
    """A count with its noun, e.g. "1 part" or "8 parts"."""
    return f"{number} {noun}{'' if number == 1 else 's'}"


@dataclass
class Part:
    """One report process: its experiments, its log and how it ended."""
    number: int
    names: list[str]
    log: Path
    proc: subprocess.Popen | None = None
    started: float = 0.0
    minutes: float = 0.0
    status: str = "not started"         # then "running", "ok", "failed (exit N)", "stopped"


@dataclass
class Context:
    """What the preflight found, for the steps after it."""
    head: str
    branch: str | None
    remote: str | None
    env: dict[str, str]
    committed: dict
    plan: dict[str, str]
    total: int                                  # the experiments of the whole publish set
    template: dict[str, list[str]]


# ------------------------------------------------------------------------------- plumbing
def git(*args: str, check: bool = True) -> str:
    """The stripped output of one git command, run at the repository's root.

    args: e.g. ("status", "--porcelain"). check: refuse when git fails; when False, a failure
    gives "".
    Raises Refusal with git's message where check is set and git fails.
    """
    proc = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode and check:
        raise Refusal(f"git {shlex.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def rel(path: Path) -> str:
    """path from the repository's root, with forward slashes, as git names it."""
    return path.resolve().relative_to(ROOT).as_posix()


def parse_command(parser: argparse.ArgumentParser, command: str) -> argparse.Namespace:
    """A PUBLISH_SET command line parsed by hybrid.cli's parser, e.g. for "ladder --year 2025
    --days 30" a namespace with cmd "ladder" and year 2025.

    Raises Refusal where it does not parse; argparse would end the driver without a summary.
    """
    try:
        return parser.parse_args(shlex.split(command))
    except SystemExit as err:
        raise Refusal(f"hybrid.cli does not parse the publish set's command {command!r}") from err


def read_csv(path: Path):
    """A CSV as text: every cell as the file holds it, nothing parsed, nothing read as missing.
    Returns a pandas DataFrame of str."""
    import pandas as pd             # imported once check_versions has checked its version

    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_lines(path: Path) -> list[str]:
    """A text file's lines with their own line endings, so that a rewrite keeps CRLF or LF."""
    with open(path, encoding="utf-8", newline="") as handle:
        return re.findall(r"[^\n]*\n|[^\n]+$", handle.read())


def write_lines(path: Path, lines: list[str]) -> None:
    """Write lines that carry their own line endings."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))


def cells(row: str) -> list[str]:
    """A markdown table row's cells as plain text, as tests/test_readme_results.py reads them:
    a link keeps its text, and bold and code marks go.

    row: e.g. "| [`a.csv`](reports/a.csv) | 3 |". Returns e.g. ["a.csv", "3"].
    """
    plain = []
    for cell in row.strip().strip("|").split("|"):
        cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
        plain.append(cell.replace("**", "").replace("`", "").strip())
    return plain


def files_of(row: str) -> list[str]:
    """The files a table row's first cell names, e.g. ["a.csv", "a_premium.csv"] for
    "| [`a.csv`](reports/a.csv), [`a_premium.csv`](reports/a_premium.csv) | ... |"."""
    return [name.strip() for name in cells(row)[0].split(",")]


def find_table(lines: list[str], caption: str, name: str) -> tuple[int, int] | None:
    """Where the table below the one line that holds caption runs, found as the README tests
    find it: (index of its header row, index after its last row).

    lines: the file's lines. caption: e.g. "Windows of the tables". name: the file's name.
    Returns None where no line holds caption or no table with a row follows it.
    Raises Refusal where more than one line holds caption.
    """
    at = [i for i, line in enumerate(lines) if caption in line and not line.startswith("|")]
    if len(at) > 1:
        raise Refusal(f"{name} has {len(at)} lines with {caption!r}, so its table is unclear")
    if not at:
        return None
    start = at[0] + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return (start, end) if end - start > 2 else None


def listed_files(path: Path, caption: str) -> set[str] | None:
    """The files the rows of the table below caption in path name, or None where path holds
    no such table. path: e.g. SOURCE. caption: e.g. "## Files"."""
    if not path.is_file():
        return None
    lines = read_lines(path)
    span = find_table(lines, caption, path.name)
    if span is None:
        return None
    return {name for line in lines[span[0] + 2:span[1]] for name in files_of(line)}


# ------------------------------------------------------------------------------ preflight
def check_tree(args: argparse.Namespace) -> tuple[str, str | None, str]:
    """The commit to run and its branch; refuses a working tree with changes or untracked
    files, and a HEAD on another branch.

    args: the options; branch and allow_detached are read.
    Returns (commit, branch or None where HEAD is detached, a note).
    """
    changes = git("status", "--porcelain").splitlines()
    if changes:
        listed = "\n".join(f"    {line}" for line in changes[:10])
        more = f"\n    ... and {len(changes) - 10} more" if len(changes) > 10 else ""
        raise Refusal("the working tree has changes, so the tables would record git_dirty "
                      f"True; the run needs a clean checkout:\n{listed}{more}")
    head = git("rev-parse", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        if not args.allow_detached:
            raise Refusal(f"HEAD is detached; check out {args.branch}")
        return head, None, f"{head[:7]}, detached (--allow-detached), clean"
    if branch != args.branch:
        raise Refusal(f"HEAD is on {branch}, not {args.branch}: check out {args.branch}")
    return head, branch, f"{head[:7]} on {branch}, clean"


def check_remote(head: str, branch: str | None, skip: bool) -> tuple[str | None, str]:
    """The remote that holds branch at head, so that anyone who clones it finds the commit the
    tables record.

    head: the commit. branch: its branch, None where HEAD is detached. skip: do not check.
    Returns (the remote or None, a note).
    Raises Refusal where the remote lacks the branch or holds another commit on it.
    """
    if skip or branch is None:
        return None, "remote tip not checked" + (" (--no-remote-check)" if skip
                                                 else " (detached HEAD)")
    remotes = git("remote").split()
    if not remotes:
        return None, "no remote, so nothing shows that the commit is pushed"
    configured = git("config", "--get", f"branch.{branch}.remote", check=False)
    remote = configured if configured in remotes else (
        "origin" if "origin" in remotes else remotes[0])
    tip = git("ls-remote", remote, f"refs/heads/{branch}").split()
    if not tip:
        raise Refusal(f"{remote} has no branch {branch}, so nobody could find the commit the "
                      "tables would record")
    if tip[0] != head:
        raise Refusal(f"HEAD {head[:7]} is not {remote}/{branch}'s tip {tip[0][:7]}, so the "
                      f"tables would record a commit that is not the pushed tip; check out "
                      f"the tip: git fetch {remote}, then git checkout -B {branch} "
                      f"{remote}/{branch}")
    return remote, f"{remote}/{branch} is at HEAD"


def _normalised(path: str | Path) -> str:
    """A path to compare with another: absolute, links resolved, case folded where the file
    system ignores case."""
    return os.path.normcase(os.path.realpath(path))


def obsidian_vaults() -> set[str]:
    """The folders Obsidian opens as vaults on this machine, from the vault list in its
    obsidian.json (Windows, macOS or Linux), as normalised paths; empty without Obsidian.

    Raises Refusal where that list exists but cannot be read.
    """
    homes = [Path(os.environ.get("APPDATA", Path.home())),
             Path.home() / "Library" / "Application Support", Path.home() / ".config"]
    vaults = set()
    for home in homes:
        path = home / "obsidian" / "obsidian.json"
        if not path.is_file():
            continue
        try:
            listed = json.loads(path.read_text(encoding="utf-8")).get("vaults", {})
        except (OSError, ValueError) as err:
            raise Refusal(f"cannot read Obsidian's vault list {path}: {err}") from err
        vaults |= {_normalised(vault["path"]) for vault in listed.values() if "path" in vault}
    return vaults


def _auto_commit(vault: Path) -> str:
    """The settings by which an enabled Obsidian-Git plugin commits or pulls in vault on its
    own, e.g. "autoSaveInterval=10"; "" where it is off or waits to be asked.

    Raises Refusal where its settings cannot be read.
    """
    config = vault / ".obsidian"
    try:
        enabled = json.loads((config / "community-plugins.json").read_text(encoding="utf-8"))
        settings = json.loads((config / "plugins" / "obsidian-git" / "data.json")
                              .read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as err:
        raise Refusal(f"cannot read the Obsidian-Git settings of {vault}: {err}") from err
    if "obsidian-git" not in enabled:
        return ""
    # autoSaveInterval commits (and syncs) every N minutes, autoPushInterval and
    # autoPullInterval push and pull, autoBackupAfterFileChange commits after an edit, and
    # autoPullOnBoot pulls when Obsidian starts, which may be during the run
    active = []
    for key, value in settings.items():
        interval = key.endswith("Interval") and isinstance(value, (int, float)) and value > 0
        on_event = key.endswith(("AfterFileChange", "OnBoot")) and value is True
        if key.startswith("auto") and (interval or on_event):
            active.append(f"{key}={value}")
    return ", ".join(active)


def check_obsidian() -> str:
    """Refuses a clone in an Obsidian vault whose Obsidian-Git commits on its own: a commit or
    pull during the run moves HEAD, and the tables would record another commit or a dirty
    tree. The repository tracks .obsidian/, so a clone that Obsidian does not open as a vault
    is safe whatever those settings say. Returns a note."""
    vaults = obsidian_vaults()
    held = [folder for folder in (ROOT, *ROOT.parents) if _normalised(folder) in vaults]
    for folder in held:
        auto = _auto_commit(folder)
        if auto:
            raise Refusal(f"{folder} is an Obsidian vault whose Obsidian-Git commits or pulls "
                          f"on its own ({auto}); the run needs a clone outside every vault")
    if held:
        return (f"{held[0]} is an Obsidian vault, but Obsidian-Git does not commit or pull on "
                "its own")
    return "no Obsidian vault holds the clone"


def check_versions() -> str:
    """Refuses another Python than PYTHON, other versions than PINS, and a missing pytest.
    Returns the versions, as a note."""
    version = platform.python_version()
    if sys.version_info[:2] != PYTHON:
        raise Refusal(f"this is Python {version}; the tables need {PYTHON[0]}.{PYTHON[1]}")
    found = {}
    for name in PINS:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = "missing"
    wrong = [f"{name} {found[name]}" for name, pin in PINS.items() if found[name] != pin]
    if wrong:
        pins = " ".join(f"{name}=={pin}" for name, pin in PINS.items())
        raise Refusal(f"{', '.join(wrong)} installed; install exactly: "
                      f"python -m pip install {pins}")
    if importlib.util.find_spec("pytest") is None:
        raise Refusal("pytest is missing: python -m pip install pytest")
    return f"Python {version}, " + ", ".join(f"{name} {v}" for name, v in found.items())


def cpu_name() -> str:
    """The CPU's name, e.g. "Intel(R) Core(TM) i7-10510U CPU @ 1.80GHz", from Windows'
    registry, else what platform knows; for the summary and the commit's body."""
    try:
        import winreg

        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            return str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
    except (ImportError, OSError):                     # not Windows, or no such key
        return platform.processor() or platform.machine()


def check_machine() -> str:
    """Refuses a machine that computes other digits than the one the committed tables come
    from: numpy on another code path than NUMPY_PATH, or PuLP calling another CBC binary than
    the one PuLP ships and run_meta.csv records, or a CBC binary that is not there. Returns a
    note."""
    import pulp
    from numpy.lib import introspect

    from hybrid.provenance import environment

    path = introspect.opt_func_info()["sin"]["dd"]["current"]
    if path != NUMPY_PATH:
        raise Refusal(f"numpy computes sin on {path}, the committed tables on {NUMPY_PATH} (an "
                      "x86-64 CPU with AVX2 and FMA3), so the synthetic demo may not reproduce")
    cbc = environment()["cbc"]
    with open(REPORTS / "run_meta.csv", encoding="utf-8", newline="") as handle:
        recorded = {row["value"] for row in csv.DictReader(handle)
                    if (row["section"], row["item"]) == ("environment", "cbc")}
    if not cbc.startswith("pulp/") or cbc not in recorded:
        raise Refusal(f"PuLP calls the CBC {cbc!r}, the committed tables {sorted(recorded)}: "
                      "another solver build can move last digits; use Windows on x86-64")
    # the path above is where PuLP expects its binary, whether or not the file is there
    if not pulp.PULP_CBC_CMD(msg=False).available():
        raise Refusal(f"PuLP's CBC binary {cbc} is missing or cannot run (removed by a virus "
                      "scanner?); reinstall PuLP")
    return f"{cpu_name()} ({platform.machine()}), numpy on {path}, CBC {cbc}"


def check_recorded_state(head: str) -> str:
    """Refuses unless the parts will record head and git_dirty False: they ask git through
    hybrid.provenance, without the core.longpaths the driver's own git calls pass, so in a
    clone whose paths are too long for Windows they would see changes that the driver does not.

    head: the commit to run. Returns a note.
    """
    from hybrid.provenance import git_state

    state = git_state(P02)
    if state != {"git_commit": head, "git_dirty": "False"}:
        raise Refusal(f"the parts would record commit {state['git_commit'][:7]} and git_dirty "
                      f"{state['git_dirty']}, not {head[:7]} and False: git without "
                      "core.longpaths sees another state (paths too long for Windows?); the "
                      "run needs a clone at a short path, e.g. ~/worktrees/portfolio-run")
    return "the parts will record HEAD and git_dirty False"


def part_env() -> dict[str, str]:
    """The environment of the parts and of the tests: this checkout's code first on PYTHONPATH,
    and unbuffered UTF-8 output, so that a log holds every line printed before a crash."""
    return {**os.environ, "PYTHONPATH": os.pathsep.join(CHECKOUT_PATH),
            "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def check_imports(env: dict[str, str]) -> str:
    """Refuses unless a part imports hybrid and datakit from this checkout, rather than from a
    copy installed from another one. env: part_env(). Returns a note."""
    code = "import hybrid, datakit; print(hybrid.__file__); print(datakit.__file__)"
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=tempfile.gettempdir(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise Refusal(f"a part cannot import hybrid and datakit: {proc.stderr.strip()[-400:]}")
    outside = [line for line in proc.stdout.splitlines()
               if not Path(line).resolve().is_relative_to(ROOT)]
    if outside:
        raise Refusal(f"a part imports {outside}, not this checkout's code")
    return "the parts import hybrid and datakit from this checkout"


def probe_hosts(skip: bool) -> str:
    """Refuses when a host the experiments download from does not answer.

    skip: probe nothing, for a machine whose caches hold every input. Returns a note, e.g.
    "api.energy-charts.info HTTP 405, www.regelleistung.net HTTP 200".
    """
    if skip:
        return "hosts not probed (--no-network-check)"
    answers = []
    for url in HOSTS:
        host = url.split("/")[2]
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"),
                                        timeout=20) as response:
                answers.append(f"{host} HTTP {response.status}")
        except urllib.error.HTTPError as err:          # an answer: the host is reachable
            answers.append(f"{host} HTTP {err.code}")
        except OSError as err:                         # URLError, timeouts, refused sockets
            raise Refusal(f"{host} does not answer ({err}); connect, or pass --no-network-check "
                          "if every input the experiments read is cached") from err
    return ", ".join(answers)


def planned_loads(plan: dict[str, str], meta) -> dict[str, tuple[int, int | None, float]]:
    """The Energy-Charts loads the plan's experiments make, by the label run_meta.csv gives
    them: the load_year calls it records for the committed experiments, and for a new one its
    year and the year before, whole, as B3 reads the year before its window.

    plan: {experiment: command}. meta: the committed run_meta.csv.
    Returns e.g. {"load_year(2025, days=30, dt=0.25)": (2025, 30, 0.25), ...}.
    """
    from hybrid import cli
    from hybrid.config import RunConfig

    loads = {}
    rows = meta[(meta["section"] == "input_sha256") & meta["experiment"].isin(list(plan))]
    for label in rows["item"]:
        match = LOAD_CALL.fullmatch(label)
        if match:
            year, days, dt = match.groups()
            loads[label] = (int(year), None if days == "None" else int(days), float(dt))
    parser, dt = cli.build_parser(), RunConfig().dt
    for name in sorted(set(plan) - set(meta["experiment"])):
        args = parse_command(parser, plan[name])
        year = getattr(args, "year", None)
        if isinstance(year, int) and not getattr(args, "synthetic_demo", False):
            for each in (year - 1, year):
                loads.setdefault(f"load_year({each}, days=None, dt={dt})", (each, None, dt))
    return loads


def prefetch_inputs(plan: dict[str, str], meta) -> str:
    """Load every Energy-Charts frame the plan reads, one after another, from datakit's cache
    or the API, and refuse where a frame the committed tables were computed from now hashes
    otherwise. datakit writes a download into its cache without a lock, so parts that start
    side by side on an empty cache would download one file at once. And Energy-Charts revises
    its data: a cache filled on another day than the committed tables' would fail the check
    after the run, which costs the whole run, while this costs seconds.

    plan: {experiment: command}. meta: the committed run_meta.csv.
    Returns a note. Raises Refusal where a frame cannot be loaded or hashes otherwise.
    """
    from hybrid import data
    from hybrid.provenance import frame_sha256

    loads = planned_loads(plan, meta)
    rows = meta[meta["section"] == "input_sha256"]
    recorded: dict[str, set[str]] = {}
    for label, value in zip(rows["item"], rows["value"]):
        recorded.setdefault(label, set()).add(value)
    differ = []
    for label, call in sorted(loads.items()):
        try:
            frame = data.load_year(*call)
        except (OSError, ValueError) as err:           # no network and no cache, or bad data
            raise Refusal(f"Energy-Charts data {label} cannot be loaded: {err}") from err
        if label in recorded and recorded[label] != {frame_sha256(frame)}:
            differ.append(label)
    if differ:
        raise Refusal("Energy-Charts data differ from the data the committed tables were "
                      f"computed from, so the run could not reproduce them: {'; '.join(differ)}. "
                      "datakit/cache holds other responses; it needs a copy of the cache of "
                      "the machine that computed the tables")
    if not loads:
        return "no Energy-Charts data to load"
    years = ", ".join(map(str, sorted({call[0] for call in loads.values()})))
    new = sum(label not in recorded for label in loads)
    return (f"Energy-Charts {years} loaded once, before the parts start; "
            f"{_count(len(loads) - new, 'frame')} hash as run_meta.csv records"
            + (f", and {new} that no committed table read are new" if new else ""))


def prefetch_afrr(plan: dict[str, str]) -> str:
    """Load regelleistung.net's aFRR capacity results of each year an afrr experiment of the
    plan reads, one after another, for the reason prefetch_inputs gives; their loader checks
    each file against its pinned SHA-256 (aFRR Phase A's design, section 2.5).

    plan: {experiment: command}. Returns a note, "" where no afrr experiment is planned.
    Raises Refusal where the loader is missing or a year cannot be loaded.
    """
    from hybrid import cli

    parser = cli.build_parser()
    parsed = [parse_command(parser, command) for command in plan.values()]
    years = sorted({args.year for args in parsed if args.cmd == "afrr"})
    if not years:
        return ""
    try:
        from hybrid.afrr_data import load_capacity_results
    except ImportError as err:
        raise Refusal("the set holds afrr experiments, but the driver finds no "
                      f"hybrid.afrr_data.load_capacity_results to load their data: {err}") from err
    for year in years:
        try:
            load_capacity_results(year)
        except (OSError, ValueError) as err:           # no network and no cache, or a new file
            raise Refusal(f"regelleistung.net's aFRR results for {year} cannot be loaded: "
                          f"{err}") from err
    return f"regelleistung.net {', '.join(map(str, years))} loaded once and match their pins"


# ----------------------------------------------------------------------------------- plan
def committed_state() -> dict:
    """What reports/ holds at HEAD: the names of its tables, and run_meta.csv and
    window_stats.csv as text.

    Returns {"tables": {"sizing_2024.csv", ...}, "meta": frame, "windows": frame}.
    """
    folder = rel(REPORTS)
    tracked = git("ls-files", "--", folder).splitlines()
    names = {Path(path).name for path in tracked
             if path.endswith(".csv") and Path(path).parent.as_posix() == folder}
    return {"tables": names - set(SUPPORT), "meta": read_csv(REPORTS / "run_meta.csv"),
            "windows": read_csv(REPORTS / "window_stats.csv")}


def select_plan(only: list[str] | None, committed: dict) -> dict[str, str]:
    """The experiments to run: PUBLISH_SET as hybrid.report names it, or its part `only` names.

    only: experiment names, e.g. ["decompose_2024"], or None for the whole set.
    committed: committed_state().
    Returns {experiment: command}, in PUBLISH_SET's order.
    Raises Refusal for a name not in the set and, for the whole set, for a committed
    experiment the set no longer holds: its rows would stay at an older commit.
    """
    from hybrid import report

    plan = report.experiments()
    if only:
        unknown = sorted(set(only) - set(plan))
        if unknown:
            raise Refusal(f"no experiment {unknown} in PUBLISH_SET: {list(plan)}")
        return {name: command for name, command in plan.items() if name in only}
    stale = sorted(set(committed["meta"]["experiment"]) - set(plan))
    if stale:
        raise Refusal(f"run_meta.csv holds experiments that PUBLISH_SET no longer has: {stale}; "
                      "remove their tables and rows first")
    return plan


def planned_windows(plan: dict[str, str]) -> set[str]:
    """The window_stats.csv keys the plan's experiments write, e.g. {"2024", "synthetic_demo"}."""
    from hybrid import cli

    parser = cli.build_parser()
    return {cli.window_label(parse_command(parser, command)) for command in plan.values()}


def experiment_costs(plan: dict[str, str], meta) -> tuple[dict[str, float], list[str]]:
    """Each experiment's expected seconds: the wall time run_meta.csv records for it, else its
    ESTIMATED_MINUTES, else the longest wall time recorded, an estimate that errs long, so that
    an unknown experiment gets a part of its own rather than a share of a long one.

    plan: {experiment: command}. meta: the committed run_meta.csv.
    Returns ({experiment: seconds}, the experiments without a recorded wall time).
    """
    walls = meta[(meta["section"] == "run") & (meta["item"] == "wall_time_s")]
    recorded = {name: float(value) for name, value in zip(walls["experiment"], walls["value"])}
    longest = max(recorded.values(), default=600.0)
    cost = {}
    for name in plan:
        if name in recorded:
            cost[name] = recorded[name]
        else:
            cost[name] = 60.0 * ESTIMATED_MINUTES[name] if name in ESTIMATED_MINUTES else longest
    return cost, [name for name in plan if name not in recorded]


def pack_parts(plan: dict[str, str], cost: dict[str, float],
               workers: int) -> list[tuple[list[str], float]]:
    """Split the plan into at most `workers` parts of about equal length: longest first, each
    experiment into the part with the least work so far.

    plan: {experiment: command}. cost: experiment_costs(). workers: e.g. 8.
    Returns (experiments in PUBLISH_SET's order, estimated seconds) per part, longest first.
    """
    count = max(1, min(workers, len(plan)))
    names: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for name in sorted(plan, key=lambda n: (-cost[n], n)):
        i = loads.index(min(loads))
        names[i].append(name)
        loads[i] += cost[name]
    parts = [([n for n in plan if n in part], load) for part, load in zip(names, loads)]
    return sorted(parts, key=lambda part: -part[1])


# ------------------------------------------------------------------------------- template
def read_template(path: Path) -> dict[str, list[str]]:
    """The rows a template adds, by heading; {} where path does not exist.

    path: e.g. scripts/publish_p02_readme_rows.md.
    Returns e.g. {"Files": ["| `x_2025.csv` | {rows} | one rung ... |"]}.
    Raises Refusal for a heading that names no table of SECTIONS, a row before any heading and
    a separator row.
    """
    if not path.is_file():
        return {}
    rows: dict[str, list[str]] = {}
    section = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            section = line[3:].strip()
            if section not in SECTIONS:
                raise Refusal(f"{path.name}:{number}: '## {section}' names no table; the "
                              f"headings are {list(SECTIONS)}")
            rows.setdefault(section, [])
        elif line.startswith("|"):
            if section is None or re.fullmatch(r"\|[\s:|-]*", line.strip()):
                raise Refusal(f"{path.name}:{number}: a row needs a '## ' heading above it and "
                              f"text in its cells: {line}")
            rows[section].append(line.rstrip())
    return rows


def planned_additions(plan: dict[str, str], committed: dict) -> tuple[list[str], list[str]]:
    """What the plan would add to reports/: (experiments without a committed table, windows
    window_stats.csv does not hold), e.g. (["afrr_2024_fast"], ["2024-01-01_30d"])."""
    new = [name for name in plan if f"{name}.csv" not in committed["tables"]]
    return new, sorted(planned_windows(plan) - set(committed["windows"]["window"]))


def check_template(template: dict[str, list[str]], path: Path, plan: dict[str, str],
                   committed: dict) -> str:
    """Refuses, before the run, a template that cannot cover what the plan adds: a new
    experiment without its row, or with another command than PUBLISH_SET's; a row for an
    experiment that is not new; another number of window rows than new windows.

    template: read_template(path). plan: {experiment: command}. committed: committed_state().
    Returns a note.
    """
    new, windows = planned_additions(plan, committed)
    if not new and not windows:
        return "nothing new is planned" + (f"; {path.name} is not used" if template else "")
    if not template:
        raise Refusal(f"the new experiments {new} and windows {windows} need README rows: write "
                      f"{path}, in the format this script's docstring gives")
    _check_how_rows(template.get("How each table is made", []), new, plan)
    for section in ("How each table is made", "Files"):
        _check_not_listed(section, template.get(section, []), new, committed["tables"])
    rows = len(template.get("Windows of the tables", []))
    if rows != len(windows):
        raise Refusal(f"{len(windows)} new windows {windows}, but {rows} rows under "
                      f"'## Windows of the tables' in {path.name}")
    return (f"{_count(len(new), 'new experiment')} and {_count(len(windows), 'new window')}, "
            f"rows from {path.name}")


def _check_how_rows(rows: list[str], new: list[str], plan: dict[str, str]) -> None:
    """Refuses 'How each table is made' rows that do not give each new experiment one row of
    three cells, with its own table first and its command as PUBLISH_SET holds it."""
    found = {}
    for row in rows:
        parts = cells(row)
        first = files_of(row)[0].removesuffix(".csv")
        if len(parts) != 3 or first not in new or first in found:
            raise Refusal("each 'How each table is made' row gives table, window and command of "
                          f"one new experiment of {new}, once: {row}")
        want = f"python -m hybrid.cli {plan[first]}"
        if parts[2] != want:
            raise Refusal(f"{first}'s row gives the command {parts[2]!r}, not {want!r}")
        found[first] = row
    missing = [name for name in new if name not in found]
    if missing:
        raise Refusal(f"no 'How each table is made' row for the new experiments {missing}")


def _check_not_listed(section: str, rows: list[str], new: list[str], tables: set[str]) -> None:
    """Refuses rows of a section that name a committed table, a new table the section's file
    lists already, rows for a file without that table, and, for SOURCE.md's file table, a new
    experiment's table that no row names."""
    path, caption = SECTIONS[section]
    listed = listed_files(path, caption)
    if listed is None:
        if rows or path == README:
            raise Refusal(f"{path.name} has no table below {caption!r}")
        return
    named = {name for row in rows for name in files_of(row)}
    twice = sorted(named & tables) + sorted(named & listed - tables)
    if twice:
        raise Refusal(f"'## {section}' rows name tables {path.name} lists already: {twice}")
    missing = [f"{name}.csv" for name in new if f"{name}.csv" not in named]
    if section == "Files" and missing:
        raise Refusal(f"no '## Files' row names the new tables {missing}")


def check_new_tables(template: dict[str, list[str]], new: dict[str, list[str]],
                     fresh: Path) -> None:
    """Refuses, after the run and before anything is written, new tables that no row names,
    rows that name a table the run did not write, and a window section of another length than
    the new windows.

    new: new_results(). fresh: the folder the run wrote.
    """
    written = {path.name for path in fresh.glob("*.csv")}
    for section in ("How each table is made", "Files"):
        path, caption = SECTIONS[section]
        if listed_files(path, caption) is None:
            continue                                   # SOURCE.md without a file table
        named = {name for row in template.get(section, []) for name in files_of(row)}
        missing = sorted(set(new["tables"]) - named)
        unknown = sorted(named - written)
        if missing or unknown:
            raise Refusal(f"'## {section}' rows must name each new table and only tables the "
                          f"run wrote: unnamed {missing}, not written {unknown}")
    rows = len(template.get("Windows of the tables", []))
    if rows != len(new["windows"]):
        raise Refusal(f"the run wrote {len(new['windows'])} new windows {new['windows']}, but "
                      f"the template has {rows} rows under '## Windows of the tables'")


# ------------------------------------------------------------------------------------ run
def run_parts(parts: list[list[str]], stage: Path, env: dict[str, str]) -> list[Part]:
    """Run each part as one `python -m hybrid.cli report --only ...` process, all side by side,
    writing into stage/STAGED and stage/logs/part<n>.log. The first part that fails stops the
    others, and so does Ctrl+C.

    parts: experiment names per part. stage: the staging folder. env: part_env().
    Returns the parts, each with its status and minutes.
    """
    (stage / "logs").mkdir()
    runs = [Part(n, names, stage / "logs" / f"part{n}.log") for n, names in enumerate(parts, 1)]
    handles = []
    try:
        for part in runs:
            handles.append(open(part.log, "w", encoding="utf-8"))
            command = [sys.executable, "-m", "hybrid.cli", "report", "--only", *part.names,
                       "--reports-dir", STAGED]
            part.started = time.monotonic()
            part.proc = subprocess.Popen(command, cwd=stage, env=env, stdout=handles[-1],
                                         stderr=subprocess.STDOUT)
            part.status = "running"
        _wait_for(runs)
    finally:
        _stop(runs)
        for handle in handles:
            handle.close()
    return runs


def _wait_for(runs: list[Part]) -> None:
    """Wait until every part has ended, printing each end, or until the first one fails."""
    running = list(runs)
    while running:
        time.sleep(2)
        for part in [p for p in running if p.proc.poll() is not None]:
            running.remove(part)
            part.minutes = (time.monotonic() - part.started) / 60
            code = part.proc.returncode
            part.status = "ok" if code == 0 else f"failed (exit {code})"
            print(f"  part {part.number} {part.status} after {part.minutes:.1f} min", flush=True)
            if code:
                return


def _stop(runs: list[Part]) -> None:
    """Stop the parts that still run, after another one failed or on Ctrl+C."""
    for part in runs:
        if part.proc is not None and part.proc.poll() is None:
            part.proc.terminate()
            part.proc.wait()
            part.minutes = (time.monotonic() - part.started) / 60
            part.status = "stopped"


def _tail(path: Path, count: int) -> list[str]:
    """The last `count` lines of a log."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


def failure_report(runs: list[Part]) -> str:
    """Why the run stopped: each failed part's status and the end of its log."""
    lines = ["a part failed, so the others were stopped and nothing was checked"]
    for part in runs:
        if part.status.startswith("failed"):
            lines.append(f"part {part.number} {part.status}; the end of {part.log}:")
            lines += [f"    {line}" for line in _tail(part.log, 12)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------- check
def check_provenance(meta, plan: dict[str, str], head: str) -> None:
    """Refuses a run in which an experiment of the plan records no commit, another commit than
    HEAD or a dirty tree, or during which HEAD moved or the working tree changed.

    meta: the fresh run_meta.csv. plan: {experiment: command}. head: the commit run.
    """
    code = meta[meta["section"] == "code"].pivot(index="experiment", columns="item",
                                                 values="value")
    missing = sorted(set(plan) - set(code.index))
    if missing:
        raise Refusal(f"the run recorded no commit for {missing}")
    wrong = code[(code["git_commit"] != head) | (code["git_dirty"] != "False")]
    if len(wrong):
        listed = "; ".join(f"{name} {row['git_commit'][:7]} dirty {row['git_dirty']}"
                           for name, row in wrong.iterrows())
        raise Refusal(f"experiments record another commit or a dirty tree: {listed}")
    if git("rev-parse", "HEAD") != head or git("status", "--porcelain"):
        raise Refusal("HEAD moved or the working tree changed during the run; something else "
                      "writes to this clone")


def _key(key) -> str:
    """A row's key as text: "2024", or "decompose_2024 / run / command" for a tuple."""
    return " / ".join(key) if isinstance(key, tuple) else str(key)


def _first(prefix: str, items, show=_key) -> list[str]:
    """One line with the first three of items, and how many more there are; [] for none."""
    items = list(items)
    if not items:
        return []
    more = f"; and {len(items) - 3} more" if len(items) > 3 else ""
    return [f"{prefix} " + "; ".join(show(item) for item in items[:3]) + more]


def compare_tables(name: str, old, new) -> list[str]:
    """How a fresh table differs from its committed one, cell for cell as text, apart from
    TIMING_COLUMNS: one line per column that differs, with its first rows.

    name: e.g. "sizing_2024.csv". old, new: the committed and the fresh table, read_csv().
    Returns [] where they agree.
    """
    if list(old.columns) != list(new.columns):
        gone = [c for c in old.columns if c not in new.columns]
        added = [c for c in new.columns if c not in old.columns]
        return [f"{name}: columns differ; only committed {gone}, only fresh {added}"
                + ("" if gone or added else ", so their order")]
    if len(old) != len(new):
        return [f"{name}: {len(old)} rows committed, {len(new)} fresh"]
    lines = []
    for column in [c for c in old.columns if c not in TIMING_COLUMNS]:
        rows = (old[column] != new[column]).to_numpy().nonzero()[0]
        lines += _first(f"{name}: {column} differs in {len(rows)} of {len(old)} rows:", rows,
                        lambda i: f"row {i} {old[column].iat[i]} -> {new[column].iat[i]}")
    return lines


def compare_keyed(name: str, old, new, keys: list[str]) -> list[str]:
    """How fresh rows differ from committed ones, where both hold rows of one experiment or
    window (the first key): rows only one of them holds, and cells that differ.

    name: e.g. "window_stats.csv". old, new: read_csv() frames. keys: the columns that name a
    row, e.g. ["experiment", "section", "item"]. Returns [] where they agree.
    """
    shared = set(old[keys[0]]) & set(new[keys[0]])
    old = old[old[keys[0]].isin(list(shared))].set_index(keys)
    new = new[new[keys[0]].isin(list(shared))].set_index(keys)
    if list(old.columns) != list(new.columns):
        return [f"{name}: columns differ: committed {list(old.columns)}, fresh {list(new.columns)}"]
    if not (old.index.is_unique and new.index.is_unique):
        return [f"{name}: two rows share a key of {keys}"]
    lines = _first(f"{name}: rows only committed:", old.index.difference(new.index))
    lines += _first(f"{name}: rows only fresh:", new.index.difference(old.index))
    both = old.index.intersection(new.index)
    for column in old.columns:
        bad = both[(old.loc[both, column] != new.loc[both, column]).to_numpy()]
        lines += _first(f"{name}: {column} differs in {len(bad)} rows:", bad,
                        lambda k: f"{_key(k)} {old.at[k, column]} -> {new.at[k, column]}")
    return lines


def compared_meta(meta):
    """run_meta.csv's rows that the check compares: all but RUN_META_EXCLUDED."""
    whole = {section for section, item in RUN_META_EXCLUDED if item is None}
    keep = [section not in whole and (section, item) not in RUN_META_EXCLUDED
            for section, item in zip(meta["section"], meta["item"])]
    return meta[keep]


def check_reproduction(fresh: Path, committed: dict,
                       full: bool) -> tuple[list[str], str, int]:
    """Compare each committed table the run wrote again, and the rows of window_stats.csv and
    run_meta.csv it wrote again, with the fresh ones.

    fresh: the folder the run wrote. committed: committed_state(). full: the whole set ran, so
    a committed table it did not write differs too.
    Returns (one line per difference, a note on what was compared, the tables compared).
    """
    written = {path.name for path in fresh.glob("*.csv")} - set(SUPPORT)
    again = sorted(committed["tables"] & written)
    diffs = []
    for name in again:
        diffs += compare_tables(name, read_csv(REPORTS / name), read_csv(fresh / name))
    if full:
        diffs += [f"{name}: committed, but the publish set did not write it"
                  for name in sorted(committed["tables"] - written)]
    windows, meta = read_csv(fresh / "window_stats.csv"), read_csv(fresh / "run_meta.csv")
    diffs += compare_keyed("window_stats.csv", committed["windows"], windows, ["window"])
    diffs += compare_keyed("run_meta.csv", compared_meta(committed["meta"]),
                           compared_meta(meta), ["experiment", "section", "item"])
    shared = set(windows["window"]) & set(committed["windows"]["window"])
    ran = set(meta["experiment"]) & set(committed["meta"]["experiment"])
    return diffs, (f"{len(again)} committed tables reproduced apart from "
                   f"{', '.join(sorted(TIMING_COLUMNS))}; window_stats.csv for {len(shared)} "
                   f"windows; run_meta.csv for {len(ran)} experiments, apart from commit, "
                   "versions, part, start and wall time"), len(again)


def new_results(fresh: Path, committed: dict) -> dict[str, list[str]]:
    """What the run adds to reports/.

    Returns {"tables": names, "windows": keys, "experiments": names}, e.g. {"tables":
    ["x_2025.csv"], "windows": [], "experiments": ["x_2025"]}.
    """
    tables = {path.name for path in fresh.glob("*.csv")} - set(SUPPORT) - committed["tables"]
    windows = set(read_csv(fresh / "window_stats.csv")["window"])
    experiments = set(read_csv(fresh / "run_meta.csv")["experiment"])
    return {"tables": sorted(tables),
            "windows": sorted(windows - set(committed["windows"]["window"])),
            "experiments": sorted(experiments - set(committed["meta"]["experiment"]))}


# -------------------------------------------------------------------------------- install
def _with_count(line: str, counts: dict[str, int]) -> str:
    """A file table's row with its Rows cell, the second, set to counts[file], where the row
    names one file of counts and that cell holds a number; else the row as it is."""
    names = files_of(line)
    parts = line.split("|")
    if (len(names) != 1 or names[0] not in counts or len(parts) < 4
            or not re.fullmatch(r"\s*[\d,]+\s*", parts[2])):
        return line
    parts[2] = f" {counts[names[0]]:,} "
    return "|".join(parts)


def edit_table(path: Path, caption: str, rows: list[str],
               counts: dict[str, int] | None = None) -> bool:
    """Add rows to the table below caption in path, before the row of run_meta.csv or
    window_stats.csv where it has one and else at its end, and write each of counts into the
    Rows cell of the row of its file. Line endings stay as the file has them.

    path: e.g. README. caption: e.g. "## Files". rows: markdown rows without line endings.
    counts: e.g. {"run_meta.csv": 1534}, or None.
    Returns whether the file changed. Raises Refusal where path has no such table.
    """
    lines = read_lines(path)
    span = find_table(lines, caption, path.name)
    if span is None:
        raise Refusal(f"{path.name} has no table below {caption!r}")
    start, end = span
    before = list(lines)
    for i in range(start + 2, end):
        lines[i] = _with_count(lines[i], counts or {})
    at = next((i for i in range(start + 2, end) if set(files_of(lines[i])) & set(SUPPORT)), end)
    ending = "\r\n" if lines[start].endswith("\r\n") else "\n"
    if not lines[at - 1].endswith("\n"):
        lines[at - 1] += ending                        # a table that ends the file
    lines[at:at] = [row + ending for row in rows]
    if lines == before:
        return False
    write_lines(path, lines)
    return True


def _rows_text(files: list[str], fresh: Path) -> str:
    """The Rows cell for a file table's row of files: "16", "8 each", or "8, 9"."""
    counts = [len(read_csv(fresh / name)) for name in files]
    if len(set(counts)) == 1:
        return f"{counts[0]:,}" + (" each" if len(counts) > 1 else "")
    return ", ".join(f"{count:,}" for count in counts)


def apply_rows(template: dict[str, list[str]], fresh: Path) -> None:
    """Write the template's rows into README.md and SOURCE.md, with "{rows}" filled in from
    the fresh tables, and the installed row counts of run_meta.csv and window_stats.csv into
    SOURCE.md's file table, where SOURCE.md has one."""
    for section, (path, caption) in SECTIONS.items():
        rows, counts = template.get(section, []), None
        if path == SOURCE:
            if listed_files(path, caption) is None:
                continue
            rows = [row.replace("{rows}", _rows_text(files_of(row), fresh)) if "{rows}" in row
                    else row for row in rows]
            counts = {name: len(read_csv(REPORTS / name)) for name in SUPPORT}
        if rows or counts:
            edit_table(path, caption, rows, counts)


def run_tests(stage: Path, env: dict[str, str]) -> str:
    """Run the P02 suite on the installed tables; its output goes to stage/logs/tests.log.

    Returns pytest's last line, e.g. "190 passed in 95.21s".
    Raises Refusal with the end of the log where a test fails.
    """
    log = stage / "logs" / "tests.log"
    command = [sys.executable, "-m", "pytest", rel(P02 / "tests"), "-q", "-p", "no:cacheprovider"]
    with open(log, "w", encoding="utf-8") as handle:
        code = subprocess.run(command, cwd=ROOT, env=env, stdout=handle,
                              stderr=subprocess.STDOUT).returncode
    tail = _tail(log, 15)
    if code:
        raise Refusal(f"the P02 tests fail with the new tables (exit {code}), so every file went "
                      f"back to its committed state; the end of {log}:\n"
                      + "\n".join(f"    {line}" for line in tail))
    return tail[-1].strip("= ") if tail else "passed"


def restore(paths: list[Path]) -> None:
    """Put each path back as HEAD holds it, in the index and the working tree, and remove the
    ones HEAD does not hold, which the install added."""
    names = [rel(path) for path in paths]
    kept = set(git("ls-tree", "-r", "--name-only", "HEAD", "--", *names).splitlines())
    if kept:
        git("restore", "--source=HEAD", "--staged", "--worktree", "--", *sorted(kept))
    for path, name in zip(paths, names):
        if name not in kept:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", name)
            path.unlink(missing_ok=True)


def install(ctx: Context, fresh: Path, stage: Path, message: tuple[str, str]) -> str:
    """Put the fresh tables, run_meta.csv and window_stats.csv into reports/ and the template's
    rows into README.md and SOURCE.md, run the P02 suite, and commit all of it as one commit.
    On any failure, every file goes back to its committed state before the error goes on.

    message: the commit's subject and body. Returns the suite's result line.
    """
    from hybrid import tables

    touched = [README] + ([SOURCE] if SOURCE.is_file() else [])
    try:
        for path in sorted(fresh.glob("*.csv")):
            target = REPORTS / path.name
            touched.append(target)
            if path.name in SUPPORT:              # keeps the rows of experiments not rerun
                tables.merge_csv(target, read_csv(path).to_dict("records"), SUPPORT[path.name])
            else:
                shutil.copyfile(path, target)
        apply_rows(ctx.template, fresh)
        git("add", "--", *[rel(path) for path in touched])
        if not git("diff", "--cached", "--name-only"):
            raise Refusal("the install changed no file, so there is nothing to commit")
        result = run_tests(stage, ctx.env)
        git(*IDENTITY, "commit", "-q", "-m", message[0], "-m", message[1])
    except BaseException:
        restore(touched)
        raise
    return result


def commit_message(ctx: Context, parts: int, minutes: float, new: dict[str, list[str]],
                   subject: str, full: bool) -> tuple[str, str]:
    """The commit's subject and body: what ran, where and how long, and what is new.

    parts: the number of parts. minutes: how long they ran. new: new_results().
    """
    what = ("the whole publish set" if full
            else f"{_count(len(ctx.plan), 'experiment')} of the publish set")
    body = (f"Rerun {what} at {ctx.head[:7]}, in {_count(parts, 'parallel part')} on "
            f"{cpu_name()} ({os.cpu_count()} logical CPUs), Python "
            f"{platform.python_version()}, in {minutes:.0f} min, so that every table it wrote "
            "and run_meta.csv record that one commit. Every committed table reproduced cell for "
            "cell apart from its timing columns, and so did every committed row of "
            "window_stats.csv and run_meta.csv apart from commit, versions, part, start and "
            "wall time.")
    if new["tables"]:
        body += f" New tables: {', '.join(new['tables'])}."
    if new["windows"]:
        body += f" New windows: {', '.join(new['windows'])}."
    return subject, textwrap.fill(body, 72)


# ----------------------------------------------------------------------------------- main
def preflight(args: argparse.Namespace, summary: Summary) -> Context:
    """Every check before the run, noted in summary. Returns what the run needs."""
    head, branch, text = check_tree(args)
    summary.add("git", text)
    remote, text = check_remote(head, branch, args.no_remote_check)
    summary.add("remote", text)
    summary.add("obsidian", check_obsidian())
    summary.add("python", check_versions())
    summary.add("machine", check_machine())
    summary.add("recorded", check_recorded_state(head))
    env = part_env()
    summary.add("imports", check_imports(env))
    committed = committed_state()
    plan = select_plan(args.only, committed)
    template = read_template(args.template)
    summary.add("template", check_template(template, args.template, plan, committed))
    if not any(planned_additions(plan, committed)):
        template = {}                   # nothing new: the rows of a template are not used
    summary.add("network", probe_hosts(args.no_network_check))
    summary.add("inputs", prefetch_inputs(plan, committed["meta"]))
    afrr = prefetch_afrr(plan)
    if afrr:
        summary.add("aFRR data", afrr)
    from hybrid import report                   # once check_versions has checked its imports

    total = len(report.experiments())
    return Context(head, branch, remote, env, committed, plan, total, template)


def run_set(ctx: Context, args: argparse.Namespace,
            summary: Summary) -> tuple[Path, int, float]:
    """Run the plan in parts into a new staging folder, noting each part.
    Returns (the folder the parts wrote, the number of parts, the minutes they ran).
    Raises Refusal where one failed."""
    cost, unrecorded = experiment_costs(ctx.plan, ctx.committed["meta"])
    parts = pack_parts(ctx.plan, cost, args.workers)
    scope = "the whole publish set" if not args.only else "a part of the set (--only)"
    guessed = (f"; {_count(len(unrecorded), 'experiment')} without a recorded wall time "
               "estimated" if unrecorded else "")
    summary.add("plan", f"{_count(len(ctx.plan), 'experiment')}, {scope}, in "
                f"{_count(len(parts), 'part')} side by side; {os.cpu_count()} CPUs "
                f"({platform.platform()}); the longest part about {parts[0][1] / 60:.0f} min, "
                f"as the recorded wall times estimate it{guessed}")
    for number, (names, seconds) in enumerate(parts, 1):
        print(f"    part {number}  ~{seconds / 60:4.1f} min  {' '.join(names)}", flush=True)
    stage = Path(tempfile.mkdtemp(prefix="publish_p02_"))
    if stage.resolve().is_relative_to(ROOT):
        raise Refusal(f"the temp folder {stage} lies in the clone; set TEMP to a folder outside it")
    summary.add("staging", f"{stage} (logs/, {STAGED}/)")
    started = time.monotonic()
    runs = run_parts([names for names, _ in parts], stage, ctx.env)
    for part in runs:
        summary.add(f"part {part.number}",
                    f"{part.minutes:5.1f} min  {part.status:<8} {' '.join(part.names)}")
    if any(part.status != "ok" for part in runs):
        raise Refusal(failure_report(runs))
    return stage, len(parts), (time.monotonic() - started) / 60


def publish(args: argparse.Namespace, summary: Summary) -> str:
    """Preflight, run, check and install, as the docstring of this script says.
    Returns the result line."""
    ctx = preflight(args, summary)
    if args.preflight_only:
        return "preflight passed (--preflight-only); nothing was run or written"
    stage, parts, minutes = run_set(ctx, args, summary)
    fresh = stage / STAGED
    others = sorted(path.name for path in fresh.iterdir() if path.suffix != ".csv")
    if others:
        raise Refusal(f"the run wrote files that are not tables, which the driver would not "
                      f"install: {others}")
    check_provenance(read_csv(fresh / "run_meta.csv"), ctx.plan, ctx.head)
    diffs, checked, compared = check_reproduction(fresh, ctx.committed, full=not args.only)
    if diffs:
        shown = "\n".join(diffs[:20]) + (f"\n... and {len(diffs) - 20} more" if len(diffs) > 20
                                          else "")
        raise Refusal(f"the run does not reproduce the committed results; nothing was "
                      f"written:\n{shown}")
    summary.add("check", checked)
    new = new_results(fresh, ctx.committed)
    if args.only and not args.allow_partial:
        if any(new.values()):
            raise Refusal(f"--only ran a part of the set, and a part is never published: its "
                          f"tables would record another commit than the others. New: {new}")
        return (f"nothing new: the {_count(compared, 'committed table')} this check of "
                f"{len(ctx.plan)} of the set's {ctx.total} experiments wrote reproduced; "
                "nothing was written")
    # The whole set goes on to the install even where nothing is new: the point of the run is
    # that run_meta.csv then records this one commit for every table.
    check_new_tables(ctx.template, new, fresh)
    if any(new.values()):
        summary.add("new", f"tables {new['tables']}, windows {new['windows']}")
    subject = args.message or (SUBJECT_NEW if new["tables"] else SUBJECT_RERUN)
    message = commit_message(ctx, parts, minutes, new, subject, full=not args.only)
    summary.add("tests", install(ctx, fresh, stage, message))
    commit = git("rev-parse", "--short", "HEAD")
    push = (f"Push it with: git push {ctx.remote} {ctx.branch}." if ctx.remote and ctx.branch
            else "No remote branch was checked, so there is no push command.")
    return (f"committed {commit} \"{subject}\"; nothing is pushed yet. {push} SOURCE.md's "
            "prose still describes the run before this one.")


def reports_state() -> str:
    """Whether reports/ and README.md match HEAD, for the summary's last line."""
    head = git("rev-parse", "--short", "HEAD", check=False)
    if not head:
        return "unknown: git cannot read the clone"
    changed = git("status", "--porcelain", "--", rel(REPORTS), rel(README), check=False)
    if changed:
        return "NOT as committed: " + "; ".join(changed.splitlines())
    return f"as committed at {head}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """The options; see --help. argv: e.g. ["--workers", "6"], or None for the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                        help="parts side by side, at most (default: the CPUs, %(default)s)")
    parser.add_argument("--branch", default="p02-afrr",
                        help="the branch HEAD must be on (default: %(default)s)")
    parser.add_argument("--template", type=Path, default=TEMPLATE,
                        help="the README rows of new tables (default: %(default)s)")
    parser.add_argument("--message", help=f"the commit's subject (default: {SUBJECT_NEW!r} "
                        f"where the run adds tables, else {SUBJECT_RERUN!r})")
    parser.add_argument("--no-network-check", action="store_true",
                        help="probe no host, where the caches hold every input")
    parser.add_argument("--preflight-only", action="store_true",
                        help="run every check before the run, the template's and the inputs' "
                        "hashes included, then stop: seconds, and nothing is written")
    parser.add_argument("--only", nargs="+", metavar="EXPERIMENT",
                        help="run these experiments only: a check, which publishes nothing")
    test = parser.add_argument_group("to test the driver, never for the published run")
    test.add_argument("--no-remote-check", action="store_true",
                      help="do not compare HEAD with the remote branch's tip")
    test.add_argument("--allow-detached", action="store_true", help="accept a detached HEAD")
    test.add_argument("--allow-partial", action="store_true",
                      help="publish what --only ran, as a run of the whole set would, though "
                      "its tables then record another commit than the others")
    args = parser.parse_args(argv)
    if (args.message and len(args.message) > 72) or args.workers < 1:
        parser.error("--message takes at most 72 characters, and --workers at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    """Publish, then print the summary. Returns the exit status: 0 for a commit, a check that
    found nothing new or a passed preflight; 1 when the driver stopped."""
    for stream in (sys.stdout, sys.stderr):
        # A pipe or a file gets Windows' ANSI code page, so one character outside it, in a
        # log tail say, would end the driver before its summary.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = parse_args(argv)
    summary = Summary()
    status = 1
    try:
        summary.add("result", publish(args, summary), show=False)
        status = 0
    except Refusal as stop:
        summary.add("STOPPED", str(stop), show=False)
    except KeyboardInterrupt:
        summary.add("STOPPED", "interrupted (Ctrl+C); the parts were stopped", show=False)
    except Exception as err:            # a bug or a state no check foresaw: still a summary
        sys.stdout.flush()
        traceback.print_exc()
        sys.stderr.flush()
        summary.add("STOPPED", f"unexpected {type(err).__name__}: {err}; the traceback is "
                    "printed above the summary", show=False)
    finally:
        summary.add("reports", reports_state(), show=False)
        summary.print()
    return status


if __name__ == "__main__":
    raise SystemExit(main())

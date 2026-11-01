"""What a result was computed from: the code's commit, the software versions, and a hash of
each input frame. The report records them next to every table (`hybrid.report`), so that a
rerun can tell a change of code or of Energy-Charts' data from noise.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def frame_sha256(frame: pd.DataFrame) -> str:
    """SHA-256 of a frame's index and values, independent of how any pandas version prints it.

    It hashes the index as little-endian int64 (nanoseconds since 1970 for timestamps), then
    for each column its name in UTF-8 and its values as little-endian float64, in column order.
    frame: e.g. a `load_year` frame. Returns the hex digest.
    """
    index = frame.index
    stamps = index.as_unit("ns").asi8 if isinstance(index, pd.DatetimeIndex) else index
    digest = hashlib.sha256(np.asarray(stamps, dtype="<i8").tobytes())
    for name in frame.columns:
        digest.update(str(name).encode())
        digest.update(np.asarray(frame[name], dtype="<f8").tobytes())
    return digest.hexdigest()


def git_state(path: Path, output: Path | None = None) -> dict[str, str]:
    """The commit of the repository that holds `path`, and whether its files differ from it.

    path: a folder inside the repository, e.g. the project folder.
    output: a folder whose changes do not count, e.g. the reports folder being written.
    Returns {"git_commit": hash, "git_dirty": "True" or "False"}; both are "unknown" where git
    cannot tell, because it is not installed or `path` lies in no repository.
    """
    try:
        root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
        commit = _git(path, "rev-parse", "HEAD")
        spec = ["."]
        if output is not None and output.resolve().is_relative_to(root):
            spec.append(f":(exclude){output.resolve().relative_to(root).as_posix()}")
        changes = _git(root, "status", "--porcelain", "--", *spec)
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"git state unknown, recorded as 'unknown': {err}", file=sys.stderr)
        return {"git_commit": "unknown", "git_dirty": "unknown"}
    return {"git_commit": commit, "git_dirty": str(bool(changes))}


def _git(cwd: Path, *args: str) -> str:
    """The stripped output of one git command run in `cwd`."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def environment() -> dict[str, str]:
    """The versions a result was computed with, and the CBC binary PuLP calls: relative to
    PuLP's package where it is the binary PuLP ships, so that no local folder is recorded.

    Returns {"python", "numpy", "pandas", "pulp", "cbc"}.
    """
    import pulp

    path = pulp.PULP_CBC_CMD(msg=False).path
    cbc = "not found"
    if path:
        binary = Path(os.path.normpath(path)).resolve()
        package = Path(pulp.__file__).resolve().parent
        cbc = (f"pulp/{binary.relative_to(package).as_posix()}"
               if binary.is_relative_to(package) else str(binary))
    return {"python": platform.python_version(), "numpy": np.__version__,
            "pandas": pd.__version__, "pulp": pulp.__version__, "cbc": cbc}

"""What the report records about a result's origin: the hash of each input frame, the code's
commit and whether it was changed, and the software versions."""
from __future__ import annotations

import hashlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


def test_the_frame_hash_is_the_documented_one():
    """A reader must be able to recompute it from the docstring alone."""
    from hybrid.provenance import frame_sha256

    idx = pd.date_range("2024-01-01", periods=2, freq="15min", tz="UTC")
    frame = pd.DataFrame({"price_eur_mwh": [50.0, -1.5]}, index=idx)
    nanoseconds = np.array([1_704_067_200, 1_704_068_100], dtype="<i8") * 10 ** 9
    digest = hashlib.sha256(nanoseconds.tobytes())
    digest.update(b"price_eur_mwh")
    digest.update(np.array([50.0, -1.5], dtype="<f8").tobytes())
    assert frame_sha256(frame) == digest.hexdigest()


def test_the_frame_hash_sees_one_changed_value():
    from hybrid.provenance import frame_sha256

    idx = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    frame = pd.DataFrame({"wind_cf": [0.1, 0.2, 0.3, 0.4]}, index=idx)
    revised = frame.copy()
    revised.iloc[2, 0] = 0.3000001
    assert frame_sha256(frame) != frame_sha256(revised)
    assert frame_sha256(frame) == frame_sha256(frame.copy())


def test_git_state_is_unknown_without_git(monkeypatch, tmp_path):
    """Without git a report must still run, and say that it cannot tell."""
    from hybrid.provenance import git_state

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert git_state(tmp_path) == {"git_commit": "unknown", "git_dirty": "unknown"}


def test_the_dirty_flag_ignores_the_folder_being_written(tmp_path):
    """Otherwise every table after the first would say it came from changed code."""
    from hybrid.provenance import git_state

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    try:
        git("init", "-q")
    except (OSError, subprocess.CalledProcessError) as err:
        pytest.skip(f"git is not available: {err}")
    (tmp_path / "code.py").write_text("x = 1\n")
    git("add", "code.py")
    git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "sizing_2024.csv").write_text("a\n1\n")
    state = git_state(tmp_path, tmp_path / "reports")
    assert len(state["git_commit"]) == 40 and state["git_dirty"] == "False"
    (tmp_path / "code.py").write_text("x = 2\n")
    assert git_state(tmp_path, tmp_path / "reports")["git_dirty"] == "True"


def test_the_environment_names_the_versions_and_the_solver():
    import pulp

    from hybrid.provenance import environment

    env = environment()
    version = sys.version_info
    assert env["python"] == f"{version.major}.{version.minor}.{version.micro}"
    assert (env["numpy"], env["pandas"], env["pulp"]) == (np.__version__, pd.__version__,
                                                         pulp.__version__)
    assert "cbc" in env["cbc"].lower()

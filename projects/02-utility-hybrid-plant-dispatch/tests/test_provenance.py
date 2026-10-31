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


def calendar_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """A synthetic Berlin calendar year of quarter-hours, cut to its first `days` days."""
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    idx = pd.date_range(start, end, freq="15min", inclusive="left")
    pv = np.clip(np.sin(np.pi * (np.arange(len(idx)) * 0.25 % 24 - 6) / 12), 0, 1)
    df = pd.DataFrame({"wind_cf": 0.4, "pv_cf": pv, "pv_feed_in_cf": 0.8 * pv,
                       "price_eur_mwh": 50 + 30 * pv}, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


def b2_instead_of_b3(wind, pv, eff_price, wind_fc, pv_fc, price_fc, run, export_cap=None,
                     progress=False, premium=None):
    """Stand-in for B3, so that nothing but B2 is solved."""
    import hybrid.baselines as baselines

    return {**baselines.b2_perfect(wind, pv, eff_price, run, export_cap),
            "solve_time_s": np.array([0.1])}


def test_a_command_notes_every_frame_it_loads(monkeypatch):
    """A report hashes what a command loaded: for the ladder with B3, the window, its year,
    and the year before, which B3's planning premium and price-forecast error come from."""
    import hybrid.baselines as baselines
    import hybrid.cli as cli
    from hybrid.provenance import frame_sha256

    monkeypatch.setattr(cli, "load_year", calendar_year)
    monkeypatch.setattr(baselines, "b3_rolling", b2_instead_of_b3)
    args = cli.build_parser().parse_args(["ladder", "--year", "2025", "--days", "1"])
    args.inputs = {}
    args.func(args)

    assert {label: (rec["year"], rec["days"]) for label, rec in args.inputs.items()} == {
        "load_year(2025, days=1, dt=0.25)": (2025, 1),
        "load_year(2025, days=None, dt=0.25)": (2025, None),
        "load_year(2024, days=None, dt=0.25)": (2024, None)}
    for rec in args.inputs.values():
        assert frame_sha256(rec["frame"]) == frame_sha256(calendar_year(rec["year"], rec["days"]))


def test_the_synthetic_demo_notes_its_inputs(monkeypatch):
    import hybrid.baselines as baselines
    import hybrid.cli as cli
    from hybrid import synthetic
    from hybrid.config import PlantConfig, RunConfig

    def eight_steps():
        run = RunConfig(horizon_steps=2, plant=PlantConfig(conn_mw=20.0))
        return synthetic.DemoInputs(np.full(8, 30.0), np.zeros(8), np.full(8, 40.0), run, 8.0)

    monkeypatch.setattr(synthetic, "demo_inputs", eight_steps)
    monkeypatch.setattr(baselines, "b3_rolling", b2_instead_of_b3)
    args = cli.build_parser().parse_args(["b3-ablation", "--synthetic-demo"])
    args.inputs = {}
    args.func(args)

    (label, rec), = args.inputs.items()
    assert (label, rec["year"]) == ("synthetic.demo_inputs()", None)
    assert rec["frame"].to_dict("list") == {"wind_mw": [30.0] * 8, "pv_mw": [0.0] * 8,
                                            "price_eur_mwh": [40.0] * 8}


def test_the_environment_names_the_versions_and_the_solver():
    import pulp

    from hybrid.provenance import environment

    env = environment()
    version = sys.version_info
    assert env["python"] == f"{version.major}.{version.minor}.{version.micro}"
    assert (env["numpy"], env["pandas"], env["pulp"]) == (np.__version__, pd.__version__,
                                                         pulp.__version__)
    assert "cbc" in env["cbc"].lower()

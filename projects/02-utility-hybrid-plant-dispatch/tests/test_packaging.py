"""The project's packaging: dependency ranges in pyproject.toml that the code relies on."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_pulp_range_admits_only_releases_the_lp_runs_on():
    """solve_window uses LpVariable.dicts, which PuLP 4.0 removed, and divides an LpVariable
    by a float, which 2.7 to 3.1 reject.

    Regression test. `pulp>=2.7` let a fresh install resolve PuLP 4.
    """
    tomllib = pytest.importorskip("tomllib")
    from packaging.requirements import Requirement

    meta = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    pulp = next(req for req in map(Requirement, meta["project"]["dependencies"])
                if req.name == "pulp")
    releases = ["2.7", "2.8", "2.9", "3.1", "3.2", "3.3.0", "4.0", "4.2"]
    assert [v for v in releases if pulp.specifier.contains(v)] == ["3.2", "3.3.0"]

"""Every CLI command runs end to end. This project needs no network access at all."""
from __future__ import annotations

import pytest

from rec.cli import main

SMALL = ["--days", "2", "--members", "6"]


@pytest.mark.parametrize("argv, expect", [
    (["mechanisms"], "Allocation mechanisms"),
    (["regimes"], "Legal regimes"),
    (["policy"], "Network charge on SHARED energy"),
    (["game"], "Core stability"),
    (["correlation"], "Inter-member load correlation"),
])
def test_cli_command_runs(capsys, argv, expect):
    assert main(argv + SMALL) == 0
    assert expect in capsys.readouterr().out

"""Every CLI command runs end to end, offline, with the price loader stubbed."""
from __future__ import annotations

import numpy as np
import pytest

import psw.cli as cli


def fake_prices(year: int, days: int, dt: float) -> np.ndarray:
    n = int(days * 24 / dt)
    h = np.arange(n) * dt % 24
    return 80 + 35 * np.sin(2 * np.pi * (h - 18) / 24) + 5 * np.cos(np.arange(n) / 7)


@pytest.mark.parametrize("argv, expect", [
    (["ladder", "--days", "2", "--horizon", "32"], "B3 rolling MPC"),
    (["lambda", "--days", "2"], "Operating-mode frontier"),
    (["exempt", "--days", "2"], "s118(6) EnWG"),
])
def test_cli_command_runs(monkeypatch, capsys, argv, expect):
    monkeypatch.setattr(cli, "load_prices", fake_prices)
    assert cli.main(argv) == 0
    assert expect in capsys.readouterr().out

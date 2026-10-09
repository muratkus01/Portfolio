"""Every CLI command runs end to end, offline, with the price loader stubbed."""
from __future__ import annotations

import numpy as np
import pytest

import evc.cli as cli


def fake_prices(year: int, n_steps: int, dt: float):
    hours = (np.arange(n_steps) * dt % 24).astype(int)
    spot = 0.08 + 0.04 * np.sin(2 * np.pi * (hours - 18) / 24)
    return spot, hours


SMALL = ["--days", "2", "--connectors", "10", "--site-limit", "60"]


@pytest.mark.parametrize("argv, expect", [
    (["ladder"], "Charging-hub ladder"),
    (["modules"], "module comparison"),
    (["dimming"], "Cost of s14a dimming"),
    (["archetypes"], "Net cost EUR by archetype"),
])
def test_cli_command_runs(monkeypatch, capsys, argv, expect):
    monkeypatch.setattr(cli, "load_prices", fake_prices)
    assert cli.main(argv + SMALL) == 0
    assert expect in capsys.readouterr().out

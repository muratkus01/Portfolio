"""Every CLI command runs end to end, offline, with the data loader stubbed."""
from __future__ import annotations

import numpy as np
import pytest

import f2b.cli as cli


def fake_load(year: int, days: int, run):
    n = int(days * 24 / run.dt)
    rng = np.random.default_rng(0)
    h = np.arange(n) * run.dt % 24
    truth = np.clip(120 + 60 * np.sin(np.arange(n) / 90) + 70 * np.clip(
        np.sin(np.pi * (h - 6) / 12), 0, 1) + 8 * rng.standard_normal(n), 0, 290)
    price = 75 + 25 * np.sin(2 * np.pi * (h - 18) / 24)
    return truth, price, np.diff(truth, prepend=truth[0])


@pytest.mark.parametrize("argv, expect", [
    (["policies"], "Trading policies"),
    (["forecast"], "Forecast quality by lead time"),
    (["crps-eur"], "Spearman rank correlation"),
    (["liquidity"], "liquidity assumption"),
])
def test_cli_command_runs(monkeypatch, capsys, argv, expect):
    monkeypatch.setattr(cli, "load", fake_load)
    assert cli.main(argv + ["--days", "3"]) == 0
    assert expect in capsys.readouterr().out

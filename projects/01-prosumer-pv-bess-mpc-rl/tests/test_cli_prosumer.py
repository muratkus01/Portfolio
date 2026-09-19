"""Every CLI command runs end to end, offline, on a synthetic week in the legacy CSV format.

These exist because a CLI is the first thing a reviewer runs, and an API rename that breaks
it is invisible to unit tests that call library functions directly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prosumer.cli import main


@pytest.fixture()
def legacy_csv(tmp_path):
    """Two days in the original thesis CSV format: `;`-separated, ISO-8859-1, day-first."""
    idx = pd.date_range("2024-06-10 00:00", periods=48, freq="h")
    h = idx.hour.to_numpy()
    load = 0.3 + 0.8 * np.exp(-((h - 19) ** 2) / 5)
    pv = 4.0 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    price = 0.08 + 0.05 * np.exp(-((h - 19) ** 2) / 8)
    df = pd.DataFrame({
        "DateTime": idx.strftime("%d.%m.%Y %H:%M"),
        "Day": (np.arange(48) // 24) + 1, "Hour": h,
        "Load": load.round(4), "PV": pv.round(4), "DE_Price": price.round(5)})
    p = tmp_path / "week.csv"
    df.to_csv(p, sep=";", index=False, encoding="ISO-8859-1")
    return str(p)


@pytest.mark.parametrize("argv, expect", [
    (["legacy"], "original objective"),
    (["ladder", "--horizon", "16"], "B3 rolling MPC"),
    (["resolution", "--fast"], "Resolution study"),
    (["modules", "--fast", "--horizon", "16"], "module comparison"),
])
def test_cli_command_runs(legacy_csv, capsys, argv, expect):
    assert main(argv[:1] + ["--data", legacy_csv] + argv[1:]) == 0
    assert expect in capsys.readouterr().out

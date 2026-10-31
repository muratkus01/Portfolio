"""Every CLI command runs end to end, offline, with the data loader stubbed."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import hybrid.cli as cli


def fake_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    n = int((days or 2) * 24 / dt)
    idx = pd.date_range(f"{year}-06-01", periods=n, freq="15min", tz="UTC")
    h = np.arange(n) * dt % 24
    return pd.DataFrame({
        "wind_cf": np.clip(0.4 + 0.3 * np.sin(np.arange(n) / 60), 0, 1),
        "pv_cf": 0.7 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1),
        "price_eur_mwh": 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 40 * (h == 13),
    }, index=idx)


@pytest.mark.parametrize("argv, expect", [
    (["ladder", "--horizon", "16"], "Hybrid plant ladder"),
    (["sizing"], "Connection-capacity sweep"),
    (["negprice"], "Negative-price rule"),
    (["decompose"], "battery's value comes from"),
])
def test_cli_command_runs(monkeypatch, capsys, argv, expect):
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(argv + ["--days", "2"]) == 0
    assert expect in capsys.readouterr().out

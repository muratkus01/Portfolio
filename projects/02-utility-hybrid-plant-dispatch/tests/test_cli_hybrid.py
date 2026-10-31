"""Every CLI command runs end to end, offline, with the data loader stubbed."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import hybrid.cli as cli
from hybrid.config import NEGATIVE_PRICE_RULES


def fake_year(year: int, days: int | None = None, dt: float = 0.25) -> pd.DataFrame:
    """A synthetic Berlin calendar year, cut to its first `days` days as load_year cuts it.

    The whole year is there because the premium is paid against realised market values of
    whole periods, and B3 plans on the month before its window, December of the year before.
    """
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Berlin").tz_convert("UTC")
    idx = pd.date_range(start, end, freq=f"{int(dt * 60)}min", inclusive="left")
    n = len(idx)
    h = np.arange(n) * dt % 24
    pv = 0.7 * np.clip(np.sin(np.pi * (h - 6) / 12), 0, 1)
    df = pd.DataFrame({
        "wind_cf": np.clip(0.4 + 0.3 * np.sin(np.arange(n) / 60), 0, 1),
        "pv_cf": pv,
        "pv_feed_in_cf": 0.8 * pv,
        "price_eur_mwh": 80 + 30 * np.sin(2 * np.pi * (h - 18) / 24) - 40 * (h == 13),
    }, index=idx)
    return df if days is None else df.iloc[:int(days * 24 / dt)]


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


@pytest.mark.parametrize("rule", NEGATIVE_PRICE_RULES)
def test_every_negative_price_rule_can_be_chosen(monkeypatch, capsys, rule):
    """Regression test: --neg-rule offered only none, six_hour and new_2025, so the four-hour
    rule of 2021-2022 and the 2023 vintage's yearly thresholds could not be run."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["ladder", "--fast", "--days", "2", "--neg-rule", rule]) == 0
    assert f"negative-price rule: {rule}" in capsys.readouterr().out

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


def test_sizing_prints_the_plant_and_the_peak_of_the_data_it_loads(monkeypatch, capsys):
    """The sweep's reason, how far the window's peak sits below nameplate, is printed from the
    loaded factors rather than quoted in the code, where it went stale when the data changed.

    Regression test as well: the header read "80 MW wind + MWp PV installed", as if all 80
    were wind.
    """
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["sizing", "--days", "2"]) == 0
    df = fake_year(2025, 2)
    peak = float((50.0 * df["wind_cf"] + 30.0 * df["pv_cf"]).max())
    out = capsys.readouterr().out
    assert "Connection-capacity sweep (50 MW wind + 30 MWp PV installed)" in out
    assert (f"peak combined output in the window: {peak:.1f} MW, {peak / 80.0:.3f} of installed"
            in out)


def test_ladder_names_the_units_and_basis_of_what_it_prints(monkeypatch, capsys):
    """Regression test: the energy above the connection was called unstorable, though the
    battery stores part of it; the curtailment columns had no unit; and the plant's premium
    rate, a blend on the window's own wind/PV mix, read as the plant's rate for the year."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["ladder", "--fast", "--days", "2"]) == 0
    out = capsys.readouterr().out
    assert "unstorable" not in out and "MWh above it" in out
    assert "curt chosen MWh" in out and "curt forced MWh" in out
    assert "2025 (365 days): wind" in out and "plant on this window's wind/PV mix" in out


def window(argv: list[str]) -> tuple:
    """The window a command line evaluates, on fake_year, and its label."""
    args = cli.build_parser().parse_args(argv)
    return cli.load_window(args, 0.25), cli.window_label(args)


def test_a_window_can_start_later_in_the_year(monkeypatch, capsys):
    """Every window used to start on 1 January, so a summer window could not be run. --start
    names the first day in Berlin time, and the window runs on for --days days of 24 h."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    frame, label = window(["ladder", "--year", "2025", "--start", "07-01", "--days", "2"])
    assert frame.index[0] == pd.Timestamp("2025-07-01", tz="Europe/Berlin")
    assert frame.index[-1] == pd.Timestamp("2025-07-02 23:45", tz="Europe/Berlin")
    assert label == "2025-07-01_2d"
    assert cli.main(["ladder", "--fast", "--start", "07-01", "--days", "2"]) == 0
    assert "Hybrid plant ladder, 2 days of 2025 from 07-01" in capsys.readouterr().out


def test_full_year_is_the_berlin_calendar_year(monkeypatch):
    monkeypatch.setattr(cli, "load_year", fake_year)
    frame, label = window(["sizing", "--year", "2024", "--full-year"])
    assert frame.index[0] == pd.Timestamp("2024-01-01", tz="Europe/Berlin")
    assert frame.index[-1] == pd.Timestamp("2024-12-31 23:45", tz="Europe/Berlin")
    assert label == "2024"


@pytest.mark.parametrize("argv", [["--start", "12-31", "--days", "2"], ["--days", "366"],
                                  ["--start", "07-01", "--full-year"]],
                         ids=["runs into next year", "longer than the year", "half a year"])
def test_a_window_outside_its_year_is_refused(monkeypatch, argv):
    """load_year returns one calendar year, so a longer window was cut short without a word."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    with pytest.raises(ValueError, match="2025"):
        window(["ladder", "--year", "2025", *argv])


@pytest.mark.parametrize("rule", NEGATIVE_PRICE_RULES)
def test_every_negative_price_rule_can_be_chosen(monkeypatch, capsys, rule):
    """Regression test: --neg-rule offered only none, six_hour and new_2025, so the four-hour
    rule of 2021-2022 and the 2023 vintage's yearly thresholds could not be run."""
    monkeypatch.setattr(cli, "load_year", fake_year)
    assert cli.main(["ladder", "--fast", "--days", "2", "--neg-rule", rule]) == 0
    assert f"negative-price rule: {rule}" in capsys.readouterr().out

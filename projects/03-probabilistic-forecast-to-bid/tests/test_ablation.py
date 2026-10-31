"""The parametric noise ablation and its CLI commands, offline, with the data loader stubbed."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from f2b import ablation, cli


def fake_load(year: int, days: int, run, shift_after: int | None = None):
    n = int(days * 24 / run.dt)
    index = pd.date_range(f"{year}-03-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    h = np.arange(n) * run.dt % 24
    truth = np.clip(120 + 60 * np.sin(np.arange(n) / 90) + 70 * np.clip(
        np.sin(np.pi * (h - 6) / 12), 0, 1) + 8 * rng.standard_normal(n), 0, 290)
    price = 75 + 25 * np.sin(2 * np.pi * (h - 18) / 24)
    signal = np.diff(truth, prepend=truth[0])
    if shift_after is not None:
        price = price.copy()
        price[shift_after:] += 400.0
        signal = signal.copy()
        signal[shift_after:] *= 5.0
    return index, truth, price, signal


def args(**kw):
    base = {"year": 2025, "days": 3, "spread": 3.0, "seed": 0}
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize("argv, expect", [
    (["synthetic-policies"], "Trading policies"),
    (["synthetic-forecast"], "Forecast quality by decision stage"),
    (["synthetic-score-vs-value"], "Spearman rank correlation"),
    (["synthetic-liquidity"], "liquidity assumption"),
])
def test_cli_command_runs(monkeypatch, capsys, argv, expect):
    monkeypatch.setattr(ablation, "load", fake_load)
    assert cli.main(argv + ["--days", "3"]) == 0
    assert expect in capsys.readouterr().out


def test_rebap_draw_does_not_depend_on_the_forecast_store(monkeypatch):
    """Regression: the store and the synthetic reBAP shared one generator. Any forecast model
    that draws a different amount of randomness then changed the imbalance prices it was
    scored against."""
    monkeypatch.setattr(ablation, "load", fake_load)
    a = ablation._setup(args())
    real_build = ablation.build_store

    def hungrier_build(truth, cfg, dt, max_lead, rng, capacity):
        rng.standard_normal(1000)
        return real_build(truth, cfg, dt, max_lead, rng, capacity)

    monkeypatch.setattr(ablation, "build_store", hungrier_build)
    b = ablation._setup(args())
    np.testing.assert_array_equal(a.rebap, b.rebap)


def test_newsvendor_fractile_ignores_the_evaluation_window(monkeypatch):
    """Regression: the fractile was estimated on the window it was then evaluated on."""
    train_steps = ablation.TRAIN_DAYS * 96
    monkeypatch.setattr(ablation, "load", fake_load)
    a = ablation._setup(args())
    monkeypatch.setattr(ablation, "load",
                        lambda y, d, r: fake_load(y, d, r, shift_after=train_steps))
    b = ablation._setup(args())
    assert not np.array_equal(a.rebap, b.rebap)
    assert a.tau == pytest.approx(b.tau)


def test_evaluation_excludes_the_training_days(monkeypatch):
    monkeypatch.setattr(ablation, "load", fake_load)
    s = ablation._setup(args())
    assert not s.mask[:ablation.TRAIN_DAYS * 96].any()
    assert s.mask.sum() == 3 * 96

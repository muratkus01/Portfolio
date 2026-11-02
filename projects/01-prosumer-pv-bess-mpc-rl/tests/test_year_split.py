"""Year split: capture over a period is the share of its summed gap, the periods take the right
months, and the two monthly tables must agree on B1 and B2."""
from __future__ import annotations

import pandas as pd
import pytest

from prosumer.experiments.year_split import B1, B2, B3, RL_CSV, ROLLING_CSV, run_year_split

MONTHS = pd.period_range("2025-01", "2026-09", freq="M").strftime("%Y-%m")


def _write_reports(root, b1_rl_shift: float = 0.0):
    """Monthly costs as the two committed CSVs hold them, for 2025-01 to 2026-09.

    B1 costs 0 in every month and B2 -10, except January 2025, where B2 and B3 both reach -30:
    B3 captures 100 % there and 50 % in every other month. The RL seed costs 1 EUR more than B1
    from October to February and 1 EUR less otherwise.
    """
    b2 = pd.Series(-10.0, index=MONTHS)
    b3 = pd.Series(-5.0, index=MONTHS)
    b2["2025-01"] = b3["2025-01"] = -30.0
    month = pd.PeriodIndex(MONTHS, freq="M").month
    winter = (month >= 10) | (month <= 2)
    rl_seed = pd.Series([1.0 if w else -1.0 for w in winter], index=MONTHS)
    rolling = pd.DataFrame({B1: 0.0, B2: b2, B3: b3}, index=MONTHS)
    rl = pd.DataFrame({B1: b1_rl_shift, B2: b2, "RL SAC seed 0": rl_seed, B3: b3}, index=MONTHS)
    for name, frame in ((ROLLING_CSV, rolling), (RL_CSV, rl)):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        frame.rename_axis("timestamp").to_csv(root / name)


def test_capture_over_a_period_is_the_share_of_its_summed_gap(tmp_path):
    """Jan to Sep 2025: B3 gains 70 of 110 EUR, 63.6 %. The mean of its monthly captures, 55.6 %,
    would weigh a month with 2 EUR to gain like one with 30."""
    _write_reports(tmp_path)
    split = run_year_split(tmp_path).set_index(["period", "controller"])
    assert split.loc[("January to September 2025", B3), "capture of B1->B2 %"] == pytest.approx(
        100 * 70 / 110)
    assert split.loc[("January to September 2026", B3), "capture of B1->B2 %"] == pytest.approx(50)
    assert (split.xs(B1, level="controller")["capture of B1->B2 %"] == 0).all()
    assert (split.xs(B2, level="controller")["capture of B1->B2 %"] == 100).all()


def test_each_period_takes_its_months(tmp_path):
    _write_reports(tmp_path)
    split = run_year_split(tmp_path).set_index(["period", "controller"])
    months = split.xs(B1, level="controller")["months"]
    assert months.to_dict() == {"January to September 2025": 9, "January to September 2026": 9,
                                "January to December 2025": 12, "October to February": 7,
                                "whole test period": 21}
    costlier = split.xs("RL SAC seed 0", level="controller")["months costlier than B1"]
    assert costlier["October to February"] == 7 and costlier["whole test period"] == 7
    assert costlier["January to December 2025"] == 5          # January, February, Oct to Dec


def test_writes_the_table_it_returns(tmp_path):
    _write_reports(tmp_path)
    split = run_year_split(tmp_path, out_dir=tmp_path / "year_split")
    written = pd.read_csv(tmp_path / "year_split" / "year_split.csv")
    pd.testing.assert_frame_equal(written, split.round(4), check_dtype=False)


def test_refuses_tables_that_disagree_on_b1(tmp_path):
    """B3's capture and RL's must rest on the same gap."""
    _write_reports(tmp_path, b1_rl_shift=0.5)
    with pytest.raises(ValueError, match="disagree"):
        run_year_split(tmp_path)

"""Experiment: the test period split by year, over the same calendar months.

The headline results run from January 2025 to 17 September 2026, so they hold January to
September twice and October to December once. The first nine months had hourly day-ahead
products, the second nine quarter-hour products, and the two years also differ in weather
and prices: this split shows how much the totals hide, but it does not tell the market
design apart from the year.

Nothing is rerun. The split is computed from two committed tables under `reports_dir`:

  rolling_eval_htw_H28/rolling_eval_monthly_cost.csv   B1, B2 and the deployable B3
  rl_eval_H28/rl_monthly_cost.csv                      every SAC seed with both RL changes

Capture in a period uses the headline's definition on the period's sums,
(cost_B1 - cost_X) / (cost_B1 - cost_B2), not the mean of the monthly captures, so a month
with little to gain weighs little.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .rolling_eval import MAIN_VARIANTS

B1, B2 = "B1 rule-based", "B2 perfect foresight"
B3 = MAIN_VARIANTS[0].name
ROLLING_CSV = Path("rolling_eval_htw_H28") / "rolling_eval_monthly_cost.csv"
RL_CSV = Path("rl_eval_H28") / "rl_monthly_cost.csv"


def monthly_costs(reports_dir: str | Path = "reports") -> pd.DataFrame:
    """Net cost per month of B1, B2, the deployable B3 and every SAC seed, in EUR.

    reports_dir: the folder that holds ROLLING_CSV and RL_CSV, e.g. "reports".
    Returns one row per month, labelled as in the CSVs ("2025-01"), one column per controller.
    Raises ValueError when the two tables disagree on the months or on B1 and B2, because the
    captures of B3 and of RL would then rest on different gaps.
    """
    reports = Path(reports_dir)
    rolling = pd.read_csv(reports / ROLLING_CSV, index_col=0)
    rl = pd.read_csv(reports / RL_CSV, index_col=0)
    if not rolling.index.equals(rl.index) or not rolling[[B1, B2]].equals(rl[[B1, B2]]):
        raise ValueError(f"{ROLLING_CSV} and {RL_CSV} disagree on the months or on B1 and B2")
    seeds = [c for c in rl.columns if c.startswith("RL SAC")]
    return pd.concat([rolling[[B1, B2, B3]], rl[seeds]], axis=1)


def periods(months: pd.Index) -> dict[str, pd.Index]:
    """The months of each period of the split, in the order the CSV lists them.

    months: month labels as the monthly CSVs hold them, e.g. ["2025-01", ..., "2026-09"].
    Returns {period name: its month labels}. "October to February" takes those months from
    every winter in `months`.
    """
    calendar = pd.PeriodIndex(months, freq="M")
    first_nine = calendar.month <= 9
    return {
        "January to September 2025": months[(calendar.year == 2025) & first_nine],
        "January to September 2026": months[(calendar.year == 2026) & first_nine],
        "January to December 2025": months[calendar.year == 2025],
        "October to February": months[(calendar.month >= 10) | (calendar.month <= 2)],
        "whole test period": months,
    }


def year_split(monthly: pd.DataFrame) -> pd.DataFrame:
    """Net cost, capture of the B1 to B2 gap and the months costlier than B1, per period.

    monthly: as `monthly_costs` returns it; it needs the columns B1 and B2.
    Returns one row per period and controller with the columns period, months, controller,
    net cost EUR, capture of B1->B2 % and months costlier than B1.
    """
    rows = []
    for period, months in periods(monthly.index).items():
        part = monthly.loc[months]
        total = part.sum()
        gap = total[B1] - total[B2]
        for controller in monthly.columns:
            rows.append({
                "period": period, "months": len(part), "controller": controller,
                "net cost EUR": total[controller],
                "capture of B1->B2 %": 100 * (total[B1] - total[controller]) / gap,
                "months costlier than B1": int((part[controller] > part[B1]).sum()),
            })
    return pd.DataFrame(rows)


def run_year_split(reports_dir: str | Path = "reports",
                   out_dir: str | Path | None = None) -> pd.DataFrame:
    """The year split of the committed monthly costs, written to out_dir/year_split.csv.

    reports_dir: the folder that holds the two monthly cost CSVs, e.g. "reports".
    out_dir: where to write the table, e.g. "reports/year_split"; None writes nothing.
    Returns the table `year_split` makes.
    """
    split = year_split(monthly_costs(reports_dir))
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        split.round(4).to_csv(out / "year_split.csv", index=False)
    return split

"""Migrate existing scenario parquet tables to schema with eval_days, annualised, and extrapolated column names."""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "simulations" / "results"

def migrate():
    # P01
    p01_path = RESULTS / "p01_scenarios.parquet"
    if p01_path.exists():
        df = pd.read_parquet(p01_path)
        df["eval_days"] = df["year"].apply(lambda y: 366 if y == 2024 else 365)
        df["annualised"] = False
        df.to_parquet(p01_path, index=False)
        print("Migrated P01: eval_days, annualised added. Rows:", len(df))

    # P02 has no scenario table: its grid read hourly prices as quarter-hours and scaled 14 days to
    # a year, and is retired; the project publishes its results as CSVs under
    # projects/02-utility-hybrid-plant-dispatch/reports/.

    # P03 has no scenario table: its grid swept synthetic forecast noise and is retired, and the
    # project publishes its results as CSVs under projects/03-probabilistic-forecast-to-bid/reports/.

    # P04
    p04_path = RESULTS / "p04_scenarios.parquet"
    if p04_path.exists():
        df = pd.read_parquet(p04_path)
        df["eval_days"] = 14
        df["annualised"] = False
        df.to_parquet(p04_path, index=False)
        print("Migrated P04: eval_days=14, annualised=False. Rows:", len(df))

if __name__ == "__main__":
    migrate()

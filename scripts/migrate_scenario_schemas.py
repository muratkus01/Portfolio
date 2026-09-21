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

    # P02
    p02_path = RESULTS / "p02_scenarios.parquet"
    if p02_path.exists():
        df = pd.read_parquet(p02_path)
        df["eval_days"] = 14
        df["annualised"] = True
        rename_map_02 = {
            "out_revenue_eur": "out_revenue_eur_per_year_extrapolated",
            "out_wear_cost_eur": "out_wear_cost_eur_per_year_extrapolated",
            "out_b1_revenue_eur": "out_b1_revenue_eur_per_year_extrapolated",
            "out_b2_revenue_eur": "out_b2_revenue_eur_per_year_extrapolated",
            "out_b3_revenue_eur": "out_b3_revenue_eur_per_year_extrapolated",
        }
        for old, new in rename_map_02.items():
            if old in df.columns:
                df[new] = df[old]
                df.drop(columns=[old], inplace=True)
        df.to_parquet(p02_path, index=False)
        print("Migrated P02: renamed extrapolated columns, eval_days=14, annualised=True. Rows:", len(df))

    # P03
    p03_path = RESULTS / "p03_scenarios.parquet"
    if p03_path.exists():
        df = pd.read_parquet(p03_path)
        df["eval_days"] = 14
        df["annualised"] = False
        df.to_parquet(p03_path, index=False)
        print("Migrated P03: eval_days=14, annualised=False. Rows:", len(df))

    # P04
    p04_path = RESULTS / "p04_scenarios.parquet"
    if p04_path.exists():
        df = pd.read_parquet(p04_path)
        df["eval_days"] = 14
        df["annualised"] = False
        df.to_parquet(p04_path, index=False)
        print("Migrated P04: eval_days=14, annualised=False. Rows:", len(df))

    # P05
    p05_path = RESULTS / "p05_scenarios.parquet"
    if p05_path.exists():
        df = pd.read_parquet(p05_path)
        df["eval_days"] = 14
        df["annualised"] = True
        rename_map_05 = {
            "out_revenue_eur": "out_revenue_eur_per_year_extrapolated",
            "out_curtail_mwh": "out_curtail_mwh_per_year_extrapolated",
            "out_neg_price_avoided_eur": "out_neg_price_avoided_eur_per_year_extrapolated",
            "out_b1_revenue_eur": "out_b1_revenue_eur_per_year_extrapolated",
            "out_b3_revenue_eur": "out_b3_revenue_eur_per_year_extrapolated",
        }
        for old, new in rename_map_05.items():
            if old in df.columns:
                df[new] = df[old]
                df.drop(columns=[old], inplace=True)
        df.to_parquet(p05_path, index=False)
        print("Migrated P05: renamed extrapolated columns, eval_days=14, annualised=True. Rows:", len(df))

    # P06
    p06_path = RESULTS / "p06_scenarios.parquet"
    if p06_path.exists():
        df = pd.read_parquet(p06_path)
        df["eval_days"] = df["year"].apply(lambda y: 366 if y == 2024 else 365)
        df["annualised"] = False
        df.to_parquet(p06_path, index=False)
        print("Migrated P06: eval_days, annualised added. Rows:", len(df))

if __name__ == "__main__":
    migrate()

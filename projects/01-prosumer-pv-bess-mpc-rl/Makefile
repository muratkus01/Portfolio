.PHONY: install test legacy ladder resolution modules fetch train reproduce-table1 clean

WEEK_WINTER := data/raw/legacy/09-15_12_2024.csv
WEEK_SUMMER := data/raw/legacy/12-18_08_2024.csv

install:
	pip install -e ".[rl,dev]"

test:
	python -m pytest tests/ -q -W ignore::DeprecationWarning

## Gate 1: reproduce the original smart_weekly.py objective (8.563975 EUR)
legacy:
	python -m prosumer.cli legacy --data $(WEEK_SUMMER)

## Full benchmark ladder at 15 minutes
ladder:
	python -m prosumer.cli ladder --data $(WEEK_WINTER) --dt 0.25 --horizon 96

## RQ1: hourly vs 15-minute modelling bias
resolution:
	python -m prosumer.cli resolution --data $(WEEK_WINTER)

## RQ4: which s14a EnWG network-charge module suits this site
modules:
	python -m prosumer.cli modules --data $(WEEK_WINTER) --fast

## Download DE-LU day-ahead prices (Energy-Charts, CC BY 4.0)
fetch:
	python -m prosumer.cli fetch --start 2024-01-01 --end 2025-01-01 \
		--out data/processed/spot_de_lu_2024.parquet

## Train SAC and score it against the ladder, 5 seeds
train:
	python train_rl.py --steps 100000 --seeds 5

## Regenerate the headline table in README section 0
reproduce-table1: test legacy ladder resolution

clean:
	rm -rf reports/*.csv .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +

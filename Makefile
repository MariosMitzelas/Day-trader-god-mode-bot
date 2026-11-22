.PHONY: help compile test dry run docker-build docker-up docker-down clean

help:
	@echo "Targets: compile test dry run docker-build docker-up docker-down clean"

compile:
	python3 -m py_compile bot.py ema_vwap.py live.py metrics.py \
	                    dashboard.py backtest.py webhook.py accounts.py ml_filter.py

test:
	pytest -q

dry:
	python3 live.py --once --dry-run

run:
	python3 live.py

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf __pycache__ .pytest_cache state/*.sqlite3 report.json ml_model.json

.PHONY: setup backend frontend dev test build network

VENV := backend/.venv
PY := $(VENV)/bin/python

setup: ## Create the backend venv (bundles SUMO) if missing, sync its packages, install frontend packages
	@# Safe to re-run: an existing venv is kept and only brought up to date with the requirements.
	@if command -v uv >/dev/null 2>&1; then \
		[ -x $(PY) ] || uv venv --python 3.13 $(VENV); \
		VIRTUAL_ENV=$(VENV) uv pip install -r backend/requirements-dev.txt; \
	else \
		[ -x $(PY) ] || python3 -m venv $(VENV); \
		$(PY) -m pip install -r backend/requirements-dev.txt; \
	fi
	npm --prefix frontend install

backend: ## FastAPI + live SUMO simulation on :8000
	cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

frontend: ## Vite dev server on :5173 (proxies /api and /ws to :8000)
	npm --prefix frontend run dev

dev: ## Backend and frontend together
	./scripts/dev.sh

test: ## Backend tests (start real SUMO processes, ~20 s)
	cd backend && .venv/bin/pytest -q

build: ## Type-check and build the frontend
	npm --prefix frontend run build

network: ## Regenerate the 3x3 network and demand files
	$(PY) simulation/networks/grid3x3/build_network.py
	$(PY) simulation/scenarios/downtown_grid/build_demand.py

# Single entry point for every gate in docs/ARCHITECTURE.md §13.
# A gate that is not implemented yet exits 2 with "NOT RUN", so it can never be mistaken for a pass.

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

UV ?= uv
PNPM ?= pnpm

# Python packages type-checked with mypy (strict settings come from pyproject.toml).
PY_TYPECHECK := packages/spec

define not_run
	@echo "NOT RUN: $(1) ($(2))" >&2; exit 2
endef

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Install Python and Node dependencies from the lockfiles
	$(UV) sync --locked --all-packages
	$(PNPM) install --frozen-lockfile

# --- G0 Static -------------------------------------------------------------------------------

.PHONY: lint
lint: ## G0: ruff (lint + format check) and eslint
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(PNPM) run lint

.PHONY: typecheck
typecheck: ## G0: mypy and tsc --noEmit
	@for pkg in $(PY_TYPECHECK); do echo "mypy $$pkg"; $(UV) run --frozen mypy $$pkg; done
	$(PNPM) run typecheck

.PHONY: fmt
fmt: ## Apply ruff formatting and safe fixes
	$(UV) run --frozen ruff format .
	$(UV) run --frozen ruff check --fix .

# --- Spec ------------------------------------------------------------------------------------

.PHONY: vectors
vectors: ## Regenerate packages/spec/vectors (and the signed fixtures)
	$(UV) run --frozen python packages/spec/tools/gen_vectors.py

# --- G2 Unit ---------------------------------------------------------------------------------

.PHONY: test-unit
test-unit: ## G2: unit tests
	$(UV) run --frozen pytest packages/spec

# --- Gates implemented in later phases ------------------------------------------------------

.PHONY: test-stats
test-stats: ## G2b: statistical guarantee simulation (P1)
	$(call not_run,G2b test-stats,implemented in phase P1)

.PHONY: bench
bench: ## G1: benchmark on public trajectory datasets (P1)
	$(call not_run,G1 bench,implemented in phase P1)

.PHONY: bench-sdk
bench-sdk: ## G3: SDK check() latency (P2 Python, P6 Node)
	$(call not_run,G3 bench-sdk,implemented in phase P2)

.PHONY: test-failure
test-failure: ## G4: failure-injection tests (P2)
	$(call not_run,G4 test-failure,implemented in phase P2)

.PHONY: test-db
test-db: ## G5: pgTAP isolation tests against local Supabase (P3)
	$(call not_run,G5 test-db,implemented in phase P3)

.PHONY: test-integration
test-integration: ## G6: API + worker + local Supabase (P3/P4)
	$(call not_run,G6 test-integration,implemented in phases P3 and P4)

.PHONY: test-e2e
test-e2e: ## G6b: console Playwright tests (P5)
	$(call not_run,G6b test-e2e,implemented in phase P5)

.PHONY: smoke-staging
smoke-staging: ## G7: smoke test against deployed staging (P7)
	$(call not_run,G7 smoke-staging,implemented in phase P7)

.PHONY: load-test
load-test: ## Load: sustained ingest against staging (P7)
	$(call not_run,load-test,implemented in phase P7)

.PHONY: security
security: ## G8: gitleaks, Semgrep, pip-audit, pnpm audit, Trivy (P7)
	$(call not_run,G8 security,implemented in phase P7)

.PHONY: docs-check
docs-check: ## G8: README quickstart in a clean container (P7)
	$(call not_run,G8 docs-check,implemented in phase P7)

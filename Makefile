.PHONY: help install dev backend frontend lint test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (backend + frontend)
	cd backend && pip install -e ".[dev]"
	cd frontend && npm install

backend: ## Start the FastAPI backend server
	cd backend && uvicorn job_monitor.main:app --reload --host 0.0.0.0 --port 8000

frontend: ## Start the React dev server
	cd frontend && npm run dev

dev: ## Start both backend and frontend (requires two terminals)
	@echo "Run in separate terminals:"
	@echo "  make backend"
	@echo "  make frontend"

lint: ## Run ruff linter
	cd backend && ruff check . && ruff format --check .

test: ## Run pytest
	cd backend && pytest -v --cov=job_monitor

clean: ## Remove generated files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -f backend/job_monitor.db

.PHONY: help venv install install-dev format lint type-check test test-cov test-clean clean docker-up docker-down run stop

help:
	@echo "Available commands:"
	@echo "  make venv           - Create virtual environment if it doesn't exist"
	@echo "  make install        - Install production dependencies"
	@echo "  make install-dev    - Install development dependencies"
	@echo "  make format         - Format code with black and isort"
	@echo "  make lint           - Lint code with ruff"
	@echo "  make type-check     - Run mypy type checking"
	@echo "  make test           - Run tests"
	@echo "  make test-cov       - Run tests with coverage report"
	@echo "  make test-clean     - Stop docker services after testing"
	@echo "  make clean          - Remove build artifacts and cache"
	@echo "  make docker-up      - Start MinIO with docker-compose"
	@echo "  make docker-down    - Stop docker-compose services"
	@echo "  make run            - Run NBD server"
	@echo "  make stop           - Stop running NBD server"

venv:
	@if [ ! -f venv/bin/activate ]; then \
		echo "Creating virtual environment..."; \
		python3 -m venv venv; \
		echo "Virtual environment created successfully"; \
	else \
		echo "Virtual environment already exists"; \
	fi

install: venv
	venv/bin/pip install -r requirements.txt
	venv/bin/pip install -e .

install-dev: venv
	venv/bin/pip install -r requirements-dev.txt
	venv/bin/pip install -e .

format: venv
	venv/bin/black src/ tests/
	venv/bin/isort src/ tests/

lint: venv
	venv/bin/ruff check src/ tests/

type-check: venv
	venv/bin/mypy src/

test: install-dev docker-up
	venv/bin/pytest

test-cov: install-dev docker-up
	venv/bin/pytest --cov=src --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

test-clean:
	$(MAKE) docker-down

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

docker-up:
	docker-compose up -d
	@echo "MinIO running at http://localhost:9001 (user: minioadmin, pass: minioadmin)"

docker-down:
	docker-compose down

run: venv
	venv/bin/python main.py

stop:
	@PID=$$(lsof -t -i :10809 2>/dev/null || echo ""); \
	if [ -n "$$PID" ]; then \
		kill $$PID && echo "NBD server stopped (PID $$PID)"; \
	else \
		echo "No NBD server running on port 10809"; \
	fi

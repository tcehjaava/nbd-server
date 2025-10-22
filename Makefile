.PHONY: help install install-dev format lint type-check test test-cov clean docker-up docker-down run

help:
	@echo "Available commands:"
	@echo "  make install        - Install production dependencies"
	@echo "  make install-dev    - Install development dependencies"
	@echo "  make format         - Format code with black and isort"
	@echo "  make lint           - Lint code with ruff"
	@echo "  make type-check     - Run mypy type checking"
	@echo "  make test           - Run tests"
	@echo "  make test-cov       - Run tests with coverage report"
	@echo "  make clean          - Remove build artifacts and cache"
	@echo "  make docker-up      - Start MinIO with docker-compose"
	@echo "  make docker-down    - Stop docker-compose services"
	@echo "  make run            - Run NBD server (requires MinIO running)"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

format:
	black src/ tests/
	isort src/ tests/

lint:
	ruff check src/ tests/

type-check:
	mypy src/

test:
	pytest

test-cov:
	pytest --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

docker-up:
	docker-compose up -d
	@echo "MinIO running at http://localhost:9001 (user: minioadmin, pass: minioadmin)"

docker-down:
	docker-compose down

run:
	python -m nbd_server.server

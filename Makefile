.PHONY: test lint coverage security docker-build docker-run clean

test:
	python -m pytest factory/tests/ -v --tb=short

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check factory/; \
	elif command -v flake8 >/dev/null 2>&1; then \
		flake8 factory/; \
	elif command -v pylint >/dev/null 2>&1; then \
		pylint factory/; \
	else \
		python -m pip install flake8 && flake8 factory/; \
	fi


security:
	@if command -v bandit >/dev/null 2>&1; then \
		bandit -r factory/; \
	else \
		echo "bandit не установлен, используй pip install bandit"; \
	fi

coverage:
	python -m pytest factory/tests/ --ignore=factory/tests/test_concurrency_stress.py \
		--cov=factory \
		--cov-report=term-missing \
		--cov-report=xml:coverage.xml \
		--cov-report=html:htmlcov \
		--cov-fail-under=70

docker-build:
	docker build -t fabrika2:latest .

docker-run:
	docker run --rm -p 8000:8000 fabrika2:latest

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov coverage.xml test-results
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

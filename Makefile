.PHONY: test lint bootstrap serve

test:
	pytest

lint:
	ruff check src tests sagemaker
	ruff format --check src tests sagemaker

bootstrap:
	python -m replenishment.cli bootstrap

serve:
	python -m replenishment.cli serve --bootstrap

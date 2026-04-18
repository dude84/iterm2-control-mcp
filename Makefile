.PHONY: help dev-env test lint mcpb clean
.DEFAULT_GOAL := help

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  dev-env   Create venv and install with dev dependencies"
	@echo "  test      Run tests"
	@echo "  lint      Run ruff and mypy"
	@echo "  mcpb      Build .mcpb extension for Claude Desktop"
	@echo "  clean     Remove build artifacts"

dev-env:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest tests/ -v

lint:
	.venv/bin/ruff check src/
	.venv/bin/mypy --strict src/

mcpb:
	./mcpb/build.sh

clean:
	rm -rf mcpb/dist/ dist/ build/ *.egg-info src/*.egg-info

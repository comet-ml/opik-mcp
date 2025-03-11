.PHONY: help install build test lint clean start dev precommit

# Default target
help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make build      - Build the project"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Run linter"
	@echo "  make clean      - Remove build artifacts"
	@echo "  make start      - Start the MCP server"
	@echo "  make dev        - Start the server in development mode"
	@echo "  make precommit  - Run pre-commit checks manually"

# Install dependencies
install:
	npm install

# Build the project
build:
	npm run build

# Run tests
test:
	npm test

# Run linter
lint:
	npm run lint

# Clean build artifacts
clean:
	rm -rf build
	rm -rf dist
	rm -rf coverage
	rm -rf .tmp
	rm -rf *.tsbuildinfo

# Start the server
start:
	node build/index.js

# Start in development mode
dev:
	npm run dev

# Run all checks (lint and test)
check: lint test
	@echo "All checks passed!"

# Run pre-commit checks manually
precommit:
	npm run lint && npm run test

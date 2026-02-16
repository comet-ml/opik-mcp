.PHONY: help install build test lint clean start dev check precommit start-http start-stdio test-transport

# Default target
help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make build      - Build the project"
	@echo "  make test       - Run tests"
	@echo "  make test-transport - Run transport-specific tests"
	@echo "  make lint       - Run linter"
	@echo "  make clean      - Remove build artifacts"
	@echo "  make start      - Start the MCP server"
	@echo "  make dev        - Start the server in development mode (streamable-http)"
	@echo "  make precommit  - Run pre-commit checks manually"
	@echo "  make start-http  - Start the MCP server with streamable-http transport"
	@echo "  make start-stdio - Start the MCP server with stdio transport"

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
	@echo "Starting MCP server (streamable-http dev mode)"
	@echo "  Host: $${STREAMABLE_HTTP_HOST:-127.0.0.1}"
	@echo "  Port: $${STREAMABLE_HTTP_PORT:-3001}"
	@echo "  Health: http://$${STREAMABLE_HTTP_HOST:-127.0.0.1}:$${STREAMABLE_HTTP_PORT:-3001}/health"
	@echo "  MCP: http://$${STREAMABLE_HTTP_HOST:-127.0.0.1}:$${STREAMABLE_HTTP_PORT:-3001}/mcp"
	@echo "  Access logs: $${STREAMABLE_HTTP_ACCESS_LOG:-true}"
	STREAMABLE_HTTP_ACCESS_LOG=$${STREAMABLE_HTTP_ACCESS_LOG:-true} npm run dev:http

# Run all checks (lint and test)
check: lint test
	@echo "All checks passed!"

# Run pre-commit checks manually
precommit:
	npm run lint && npm run test

# Start the MCP server with streamable-http transport
start-http:
	@echo "Starting MCP server with streamable-http transport on port 3001..."
	@echo "  Health: http://$${STREAMABLE_HTTP_HOST:-127.0.0.1}:$${STREAMABLE_HTTP_PORT:-3001}/health"
	@echo "  MCP: http://$${STREAMABLE_HTTP_HOST:-127.0.0.1}:$${STREAMABLE_HTTP_PORT:-3001}/mcp"
	@echo "  Access logs: $${STREAMABLE_HTTP_ACCESS_LOG:-false}"
	@STREAMABLE_HTTP_ACCESS_LOG=$${STREAMABLE_HTTP_ACCESS_LOG:-false} npm run start:http

# Start the MCP server with stdio transport
start-stdio:
	@echo "Starting MCP server with stdio transport..."
	@npm run start:stdio

# Run transport-specific tests
test-transport:
	npm run test:transport

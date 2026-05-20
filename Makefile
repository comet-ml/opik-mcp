.PHONY: help install run run-dev dev inspect test test-live conformance lint format typecheck check \
        legacy-install legacy-build legacy-test legacy-lint legacy-start

help:
	@echo "Python (root):"
	@echo "  make install    - uv sync --extra dev"
	@echo "  make run        - run the MCP server (stdio by default)"
	@echo "  make run-dev    - run with DEBUG logging + uvicorn reload"
	@echo "  make dev        - run via mcp inspector dev"
	@echo "  make inspect    - launch MCP Inspector against running server"
	@echo "  make test       - pytest"
	@echo "  make conformance- pytest tests/conformance (MCP wire contract)"
	@echo "  make lint       - ruff check + format check"
	@echo "  make format     - ruff format + ruff check --fix"
	@echo "  make typecheck  - mypy"
	@echo "  make check      - lint + typecheck + test"
	@echo ""
	@echo "Legacy TypeScript (legacy/typescript/, deprecated):"
	@echo "  make legacy-install - npm install in legacy/typescript"
	@echo "  make legacy-build   - tsc build in legacy/typescript"
	@echo "  make legacy-test    - jest in legacy/typescript"
	@echo "  make legacy-lint    - eslint in legacy/typescript"
	@echo "  make legacy-start   - node build/index.js in legacy/typescript"

install:
	uv sync --extra dev

run:
	uv run opik-mcp

run-dev:
	OPIK_MCP_RELOAD=1 OPIK_MCP_LOG_LEVEL=DEBUG uv run opik-mcp

dev:
	uv run mcp dev src/opik_mcp/server.py

inspect:
	npx @modelcontextprotocol/inspector

test:
	uv run pytest -q

test-live:
	RUN_LIVE_DEV_COMET=1 uv run pytest tests/test_ask_ollie_live.py -v

# Wire-contract suite -- see docs/host-conformance.md. The whole-suite
# `make check` already runs these (test target is `pytest -q`), this is
# the focused entrypoint for when you're iterating on the tool surface.
conformance:
	uv run pytest tests/conformance -v

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test

# --- Legacy TypeScript server (deprecated, kept under legacy/typescript/) ---

legacy-install:
	$(MAKE) -C legacy/typescript install

legacy-build:
	$(MAKE) -C legacy/typescript build

legacy-test:
	$(MAKE) -C legacy/typescript test

legacy-lint:
	$(MAKE) -C legacy/typescript lint

legacy-start:
	$(MAKE) -C legacy/typescript start

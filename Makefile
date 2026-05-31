.PHONY: help version install run run-dev dev inspect test test-live conformance lint format typecheck check \
        docker-build docker-run \
        legacy-install legacy-build legacy-test legacy-lint legacy-start

VERSION_FILE := src/opik_mcp/_version.py

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
	@echo "Docker:"
	@echo "  make docker-build - build opik-mcp:dev image"
	@echo "  make docker-run   - run opik-mcp:dev on :8080 (loopback)"
	@echo ""
	@echo "Legacy TypeScript (legacy/typescript/, deprecated):"
	@echo "  make legacy-install - npm install in legacy/typescript"
	@echo "  make legacy-build   - tsc build in legacy/typescript"
	@echo "  make legacy-test    - jest in legacy/typescript"
	@echo "  make legacy-lint    - eslint in legacy/typescript"
	@echo "  make legacy-start   - node build/index.js in legacy/typescript"

# Generate the git-ignored version file. CI/release pass VERSION=<x.y.z>;
# locally it falls back to <MAJOR.MINOR from version.txt>.dev0.
version:
	@printf '__version__ = "%s"\n' "$${VERSION:-$$(tr -d '[:space:]' < version.txt).dev0}" > $(VERSION_FILE)
	@echo "wrote $(VERSION_FILE): $$(cat $(VERSION_FILE))"

install: version
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

# Wire-contract suite. The whole-suite `make check` already runs these
# (test target is `pytest -q`), this is the focused entrypoint for when
# you're iterating on the tool surface.
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

check: version lint typecheck test

# --- Docker image (deployable per OPIK-6667) -------------------------------

docker-build: version
	docker build -t opik-mcp:dev .

docker-run:
	# Explicit 127.0.0.1 binding: on Linux, `-p 8080:8080` listens on
	# 0.0.0.0, which combined with the dev-token-123 default token would
	# expose MCP on every network interface of a dev VM or CI runner.
	docker run --rm -p 127.0.0.1:8080:8080 \
	  -e OPIK_MCP_DEV_TOKEN=$${OPIK_MCP_DEV_TOKEN:-dev-token-123} \
	  -e COMET_URL_OVERRIDE=$${COMET_URL_OVERRIDE:-https://www.comet.com} \
	  --name opik-mcp opik-mcp:dev

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

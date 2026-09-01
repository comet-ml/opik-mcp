.PHONY: help version install run run-dev dev inspect test test-live conformance e2e lint format typecheck check \
        skills-pack skills-verify skills-verify-source \
        docker-build docker-run \
        legacy-install legacy-build legacy-test legacy-lint legacy-start

# The skills recipes use `set -o pipefail`, which is a bash builtin. Make defaults
# to /bin/sh, which is dash on Ubuntu runners (and bash on macOS) — so without this
# those recipes pass locally and fail in CI with "Illegal option -o pipefail".
SHELL := /bin/bash

VERSION_FILE := src/opik_mcp/_version.py

# Pinned: this CLI is what ~40 agents use to install the pack, so a behaviour
# change in it changes what our verification actually proves.
SKILLS_CLI_VERSION := 1.5.22

help:
	@echo "Python (root):"
	@echo "  make install    - uv sync --extra dev"
	@echo "  make run        - run the MCP server (stdio by default)"
	@echo "  make run-dev    - run with DEBUG logging + uvicorn reload"
	@echo "  make dev        - run via mcp inspector dev"
	@echo "  make inspect    - launch MCP Inspector against running server"
	@echo "  make test       - pytest"
	@echo "  make conformance- pytest tests/conformance (MCP wire contract)"
	@echo "  make e2e        - pytest -m e2e (real stdio subprocess; not in make check)"
	@echo "  make lint       - ruff check + format check"
	@echo "  make format     - ruff format + ruff check --fix"
	@echo "  make typecheck  - mypy"
	@echo "  make check      - lint + typecheck + test"
	@echo ""
	@echo "Skills pack (published as comet-ml/opik-skills):"
	@echo "  make skills-pack          - build the pack into dist/opik-skills"
	@echo "  make skills-verify        - build it, then install it with the real npx installer"
	@echo "  make skills-verify-source - check this repo itself resolves exactly the authored skills"
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

# End-to-end: spawns `python -m opik_mcp` as a real subprocess and drives it over
# stdio. NOT part of `make test` / `make check` — `addopts` deselects the marker so
# the default suite stays in-process — so this target and the e2e_tests workflow
# are the only things that run it. It is the only suite that exercises
# `__main__`'s stdio startup path, which every MCP host actually uses. Needs no
# credentials and no backend.
#
# PYTEST_ARGS is how CI adds `--junitxml` without forking the command: the
# workflow runs this exact target, so what CI does and what you can reproduce
# locally cannot drift.
e2e:
	uv run pytest -m e2e -v $(PYTEST_ARGS)

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: version lint typecheck test

# --- Skills pack (OPIK-7621) -----------------------------------------------
#
# `opik-mcp` authors the skills; `comet-ml/opik-skills` is the generated pack
# that `npx skills add comet-ml/opik-skills` resolves to. CI publishes the built
# pack as an artifact and the public repo pulls it — nothing is hand-edited there.

SKILLS_PACK_DIR := dist/opik-skills

skills-pack:
	uv run python scripts/build_skills_pack.py --out $(SKILLS_PACK_DIR) \
	  $(if $(VERSION),--pack-version "$(VERSION)",) \
	  $(if $(SOURCE_COMMIT),--source-commit "$(SOURCE_COMMIT)",)

# Installs the built pack with the *exact* command the product's onboarding shows
# (`-g --all`), into a throwaway HOME so a global install cannot touch the real
# one, and diffs the whole installed tree against the pack. Building the pack and
# the installer resolving it are different questions; only this answers the second.
skills-verify: skills-pack
	@set -euo pipefail; \
	tmp=$$(mktemp -d); \
	trap 'rm -rf "$$tmp"' EXIT; \
	if ! out=$$(HOME="$$tmp" npx -y skills@$(SKILLS_CLI_VERSION) \
	      add "$(CURDIR)/$(SKILLS_PACK_DIR)" -g --all 2>&1); then \
	  echo "::error::installer failed on the built pack"; echo "$$out"; exit 1; \
	fi; \
	( cd "$(SKILLS_PACK_DIR)/skills" && find . -type f | sort ) > "$$tmp/expected"; \
	( cd "$$tmp/.agents/skills" 2>/dev/null && find . -type f | sort ) > "$$tmp/actual" \
	  || : > "$$tmp/actual"; \
	if ! diff -u "$$tmp/expected" "$$tmp/actual"; then \
	  echo "::error::installed tree differs from the built pack"; exit 1; \
	fi; \
	echo "installer reproduced the pack exactly ($$(wc -l < "$$tmp/expected" | tr -d ' ') files)"

# Ticket 02: installing straight from this repository must resolve exactly the
# authored skills. It already worked, but only via the installer's last-resort
# fallback — with a stray skills directory committed it resolved unrelated
# third-party skills instead. `.claude-plugin/marketplace.json` makes it explicit;
# this asserts it stays that way.
skills-verify-source:
	@set -euo pipefail; \
	tmp=$$(mktemp -d); \
	trap 'rm -rf "$$tmp"' EXIT; \
	if ! out=$$(HOME="$$tmp" npx -y skills@$(SKILLS_CLI_VERSION) \
	      add "$(CURDIR)" -g --all 2>&1); then \
	  echo "::error::installer failed against the repository"; echo "$$out"; exit 1; \
	fi; \
	resolved=$$(cd "$$tmp/.agents/skills" 2>/dev/null && ls -1 | sort | tr '\n' ' ' || true); \
	authored=$$(cd src/opik_mcp/skills && \
	  find . -mindepth 2 -maxdepth 2 -name SKILL.md -exec dirname {} \; \
	  | sed 's|^\./||' | sort | tr '\n' ' '); \
	if [ "$$resolved" != "$$authored" ]; then \
	  echo "::error::installer resolved [$$resolved] but the authored skills are [$$authored]"; \
	  exit 1; \
	fi; \
	echo "repository resolves exactly the authored skills: $$authored"

# --- Docker image (deployable per OPIK-6667) -------------------------------

docker-build: version
	docker build -t opik-mcp:dev .

docker-run:
	# Explicit 127.0.0.1 binding: on Linux, `-p 8080:8080` listens on
	# 0.0.0.0, exposing MCP on every network interface of a dev VM or CI
	# runner. opik-mcp does no local token validation (the backend does),
	# so keep the port loopback-only.
	docker run --rm -p 127.0.0.1:8080:8080 \
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

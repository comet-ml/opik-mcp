# syntax=docker/dockerfile:1.7
#
# Multi-stage build using the official `uv` image, per
# https://docs.astral.sh/uv/guides/integration/docker/. Two real wins over
# the older `pip install uv && uv pip install --system` pattern:
#
#   1. Deps and project code live in separate layers, so a code change
#      doesn't re-resolve the dependency graph.
#   2. The build stage owns uv + its caches; the final stage carries only
#      the project venv + a slim Python runtime. No uv binary, no apt
#      caches, no build tooling in the shipped image.
#
# Build stage ----------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Resolve deps from the lockfile first, WITHOUT the project, so this layer
# is reused across every code-only change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install the project itself into the venv; --no-editable so the runtime
# stage doesn't need the source tree mounted.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Runtime stage --------------------------------------------------------------

FROM python:3.13-slim-bookworm AS runtime

# tini gives us a real PID 1 that forwards SIGTERM to uvicorn promptly,
# so graceful shutdown actually completes within the K8s terminationGracePeriod.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash app

WORKDIR /app

# Copy only the venv from the build stage. No uv, no pip cache, no source
# tree in the shipped image.
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app

EXPOSE 8080
ENV OPIK_MCP_TRANSPORT=http \
    OPIK_MCP_HOST=0.0.0.0 \
    OPIK_MCP_PORT=8080 \
    OPIK_MCP_LOG_LEVEL=INFO

STOPSIGNAL SIGTERM

# Single worker by design: SSE streams are async-IO-bound, and multiple
# workers would fragment in-memory MCP session state across processes.
# Scale with replicas + Redis (see docs/phase-2.md), not workers.
ENTRYPOINT ["tini", "--", "python", "-m", "uvicorn", \
            "opik_mcp.server:build_app", "--factory", \
            "--host", "0.0.0.0", "--port", "8080", \
            "--workers", "1", \
            "--no-access-log"]

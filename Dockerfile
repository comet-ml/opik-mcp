FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash app

WORKDIR /app
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app src ./src

RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache .

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

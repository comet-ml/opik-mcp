import logging
import os
import sys

import uvicorn
from pydantic import ValidationError

from opik_mcp.analytics import (
    EVENT_SERVER_STARTED,
    EVENT_STARTUP_ERROR,
    get_analytics,
    track_event,
)
from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.config import Settings, get_settings

logger = logging.getLogger("opik_mcp")

INSECURE_DEFAULT_TOKEN = "dev-token-123"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Best-effort drain budget on the startup-error path. The daemon worker that
# normally POSTs analytics events is killed by the imminent sys.exit/re-raise,
# so we must block long enough for the in-flight POST to land — but not so
# long that a broken receiver hangs the user-facing crash.
_STARTUP_ERROR_FLUSH_DEADLINE_S = 2.0

# Bounded allowlist for ``exception_type`` on the transport-crash path. Any
# class outside this set is bucketed to ``"unknown"`` to preserve the low-
# cardinality contract used by ``_ERROR_KIND_TABLE`` in analytics/wrappers.py —
# otherwise a future uvicorn middleware exception subclass would expand the
# cardinality of this field unboundedly in BI.
_KNOWN_TRANSPORT_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "OSError",  # uvicorn EADDRINUSE / permission denied
        "ImportError",  # lazy server-module import failed
        "ModuleNotFoundError",  # subclass of ImportError, kept explicit
        "RuntimeError",  # mcp/anyio internal
        "KeyboardInterrupt",
        "SystemExit",
        "ValueError",
        "TypeError",
    }
)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _bucket_transport_exception_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if name in _KNOWN_TRANSPORT_EXCEPTION_NAMES else "unknown"


def _build_fallback_analytics_client() -> AnalyticsClient:
    """Build an AnalyticsClient without going through ``get_settings()``.

    Used on the config-validation-failure path: ``get_analytics()`` would
    call ``Settings()`` again, hit the same ``ValidationError``, and be
    swallowed by ``_emit_startup_error``'s outer ``except Exception`` — the
    BI event would silently disappear. ``model_construct()`` bypasses
    pydantic validation entirely, populating fields with their declared
    defaults, so the client can be constructed even when the user's env is
    broken.

    Analytics-relevant env vars are re-read manually so user opt-out and
    on-prem URL routing still hold on this path; ``model_construct()``
    intentionally ignores env (it's a BaseSettings escape hatch), so we
    layer those back in by hand.
    """
    settings = Settings.model_construct()
    # Respect opt-out even when the rest of config is broken. Default-true
    # mirrors the field default in ``Settings``; anything other than a
    # recognised truthy value disables emit, so a typo'd value is fail-safe.
    raw_enabled = os.getenv("OPIK_MCP_ANALYTICS_ENABLED", "true").lower()
    settings.opik_mcp_analytics_enabled = raw_enabled in {"true", "1", "yes", "on"}
    # On-prem deploys override the destination URL; without this we would
    # leak the failure event to comet.com from a self-hosted install.
    url_override = os.getenv("OPIK_MCP_ANALYTICS_URL")
    if url_override:
        settings.opik_mcp_analytics_url = url_override
    source_override = os.getenv("OPIK_MCP_ANALYTICS_SOURCE")
    if source_override is not None:
        settings.opik_mcp_analytics_source = source_override
    return AnalyticsClient(settings)


def _emit_startup_error(
    *,
    phase: str,
    error_kind: str,
    exception_type: str = "",
    transport: str = "",
    client: AnalyticsClient | None = None,
) -> None:
    """Fire ``opik_mcp_startup_error`` and synchronously drain the queue.

    PII contract: only low-cardinality, class-level fields. The exception
    *message* is deliberately NOT included — it can carry paths, env values,
    or partial secrets. ``exception_type`` (class name) plus ``error_kind``
    gives BI enough to bucket failures without leaking user data.

    When ``client`` is passed (config-fail path), use it directly and close
    it after flush; the singleton route would re-hit the underlying
    ``Settings()`` failure and drop the event.
    """
    props: dict[str, str] = {"phase": phase, "error_kind": error_kind}
    if exception_type:
        props["exception_type"] = exception_type
    if transport:
        props["transport"] = transport
    try:
        target: AnalyticsClient = client if client is not None else get_analytics()
        target.track_event(EVENT_STARTUP_ERROR, props)
        # Synchronous drain: without this the daemon worker thread is killed
        # by SystemExit / process unwind before httpx finishes the POST.
        target.flush(deadline_s=_STARTUP_ERROR_FLUSH_DEADLINE_S)
    except Exception:
        # track_event MUST NEVER mask the real startup failure — swallow any
        # analytics-side problem and let the original exception propagate.
        logger.debug("startup_error emit failed", exc_info=True)


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as e:
        # Settings construction failed — typically a bad COMET_WORKSPACE_ID
        # UUID or an unrecognised OPIK_MCP_AUTO_APPROVE literal. The singleton
        # route would re-construct Settings and re-raise; use a dedicated
        # fallback client that doesn't depend on the broken config so the
        # event still lands.
        fallback = _build_fallback_analytics_client()
        try:
            _emit_startup_error(
                phase="config",
                error_kind="invalid_config",
                exception_type=type(e).__name__,
                client=fallback,
            )
        finally:
            fallback.close()
        raise

    _configure_logging(settings.opik_mcp_log_level)
    transport = settings.opik_mcp_transport.lower()

    track_event(
        EVENT_SERVER_STARTED,
        {
            "transport": transport,
            "analytics_enabled": str(settings.opik_mcp_analytics_enabled).lower(),
            "has_workspace": str(settings.comet_workspace is not None).lower(),
            "has_api_key": str(settings.opik_api_key is not None).lower(),
            "has_default_project": str(settings.opik_default_project_name is not None).lower(),
        },
    )

    try:
        _run_transport(settings, transport)
    except SystemExit:
        # Re-raise without wrapping — the exit was deliberate and any
        # startup_error was already emitted at the decision point below.
        raise
    except BaseException as e:
        _emit_startup_error(
            phase="transport_start",
            error_kind="transport_crash",
            exception_type=_bucket_transport_exception_type(e),
            transport=transport,
        )
        raise


def _run_transport(settings: Settings, transport: str) -> None:
    if transport == "stdio":
        # Default: Claude Code (or any MCP client) launches this process and
        # speaks MCP over stdin/stdout. No port, no bearer token, no uvicorn.
        # OPIK_MCP_DEV_TOKEN is only relevant in HTTP mode (see below).
        from opik_mcp.server import mcp

        logger.info("startup transport=stdio")
        mcp.run(transport="stdio")
        return

    logger.info(
        "startup transport=http host=%s port=%s reload=%s",
        settings.opik_mcp_host,
        settings.opik_mcp_port,
        settings.opik_mcp_reload,
    )

    if settings.opik_mcp_dev_token == INSECURE_DEFAULT_TOKEN:
        if settings.opik_mcp_host not in LOOPBACK_HOSTS:
            logger.error(
                "Refusing to start: OPIK_MCP_DEV_TOKEN is the insecure default %r "
                "and OPIK_MCP_HOST=%r is not a loopback address. Set a strong "
                "OPIK_MCP_DEV_TOKEN secret, or bind to 127.0.0.1/::1/localhost.",
                INSECURE_DEFAULT_TOKEN,
                settings.opik_mcp_host,
            )
            _emit_startup_error(
                phase="http_bind_check",
                error_kind="insecure_token_on_public_iface",
                transport=transport,
            )
            sys.exit(1)
        logger.warning(
            "OPIK_MCP_DEV_TOKEN is using the insecure default %r. "
            "Set a strong secret before exposing this server beyond localhost.",
            INSECURE_DEFAULT_TOKEN,
        )

    # Imported lazily so stdio mode doesn't pay the Starlette import cost.
    from opik_mcp.server import build_app

    if settings.opik_mcp_reload:
        uvicorn.run(
            "opik_mcp.server:build_app",
            factory=True,
            host=settings.opik_mcp_host,
            port=settings.opik_mcp_port,
            reload=True,
            reload_dirs=["src"],
        )
    else:
        uvicorn.run(build_app(), host=settings.opik_mcp_host, port=settings.opik_mcp_port)


if __name__ == "__main__":
    main()

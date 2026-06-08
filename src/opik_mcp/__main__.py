import logging
import os
import socket
import sys
import time

import uvicorn
from pydantic import ValidationError

from opik_mcp import error_tracking
from opik_mcp.analytics import (
    EVENT_SERVER_SHUTDOWN,
    EVENT_SERVER_STARTED,
    EVENT_STARTUP_ERROR,
    boot_props,
    get_analytics,
    track_event,
    transport_probe,
)
from opik_mcp.analytics.boot_props import collect_boot_props
from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.analytics.environment import collect_environment_fingerprint
from opik_mcp.analytics.events import bucket_seconds
from opik_mcp.analytics.identity import install_id_was_freshly_generated
from opik_mcp.config import Settings, get_settings

logger = logging.getLogger("opik_mcp")


# Best-effort drain budget on the startup-error path. The daemon worker that
# normally POSTs analytics events is killed by the imminent sys.exit/re-raise,
# so we must block long enough for the in-flight POST to land — but not so
# long that a broken receiver hangs the user-facing crash. Widened from 2.0s
# to give a cold first POST (DNS+TLS handshake) — plus the worker's first
# retry — a fair chance to land within the deadline. flush() returns as soon
# as the queue drains, so this is a ceiling, not a fixed wait.
_STARTUP_ERROR_FLUSH_DEADLINE_S = 3.5
# Shutdown shares the same constraint (daemon worker about to be killed) but
# is intentionally a separate constant so the startup and shutdown budgets
# can evolve independently — e.g. shutdown may need a longer drain once we
# add larger trailing event payloads (lifespan stats, session summaries).
_SHUTDOWN_FLUSH_DEADLINE_S = 3.5

# Bounded allowlist for ``exception_type`` on the transport-crash path. Any
# class outside this set is bucketed to ``"unknown"`` to preserve the low-
# cardinality contract used by ``bucket_exception`` in analytics/errors.py —
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
    """Map a transport exception to the most specific bucketed name in the allowlist.

    Walks the MRO so common ``OSError`` subclasses — ``PermissionError``
    (privileged port), ``ConnectionRefusedError``, ``BrokenPipeError`` —
    bucket as ``"OSError"`` rather than expanding cardinality with each
    new subclass. Returns ``"unknown"`` only when nothing in the chain
    matches.
    """
    for cls in type(exc).__mro__:
        if cls.__name__ in _KNOWN_TRANSPORT_EXCEPTION_NAMES:
            return cls.__name__
    return "unknown"


def _preflight_bind_check(host: str, port: int) -> None:
    """Bind+release the target socket before handing it to uvicorn.

    Uvicorn handles bind failures (EADDRINUSE, permission denied, …)
    *internally*: it logs an ``ERROR``, runs the ASGI shutdown lifespan,
    and returns normally from ``uvicorn.run`` with exit code 0. Our outer
    ``except BaseException`` never sees the ``OSError``, so the most common
    real-world transport failure — port already in use — was invisible to
    BI before this check.

    Performing the bind ourselves surfaces the ``OSError`` to the caller,
    which our error handler then tags as ``transport_crash`` and re-raises.

    Address-family handling: ``getaddrinfo`` resolves ``host`` to the right
    ``(AF_INET / AF_INET6, sockaddr)`` so this works for ``127.0.0.1``,
    ``::1``, and ``localhost`` (which resolves to ``::1`` first on macOS
    15+ and many modern Linux distros). Hardcoding ``AF_INET`` would
    either raise a spurious ``Invalid argument`` for ``::1`` (false-
    positive crash) or silently pass when uvicorn would actually fail.

    The window between releasing this socket and uvicorn binding it is a
    benign race: a process that grabs the port in that gap will still
    cause uvicorn to log an error — we just miss the BI event for that
    extremely narrow case. On Linux there is also a ``SO_REUSEPORT``
    asymmetry: another process holding the port with only
    ``SO_REUSEPORT`` can let our preflight succeed while uvicorn's
    actual bind fails. Both gaps are accepted in exchange for catching
    the common cases (port already taken at startup, privileged port
    without permission).
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not infos:
        return  # Nothing resolved; let uvicorn report any failure itself.
    af, socktype, proto, _canonname, sockaddr = infos[0]
    sock = socket.socket(af, socktype, proto)
    try:
        # SO_REUSEADDR matches uvicorn's own bind so we don't reject a port
        # uvicorn would have accepted (e.g. a recently-closed TIME_WAIT).
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(sockaddr)
    finally:
        sock.close()


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
    oauth_configured: str = "",
) -> None:
    """Fire ``opik_mcp_startup_error`` and synchronously drain the queue.

    PII contract: only low-cardinality, class-level fields. The exception
    *message* is deliberately NOT included — it can carry paths, env values,
    or partial secrets. ``exception_type`` (class name) plus ``error_kind``
    gives BI enough to bucket failures without leaking user data.

    ``oauth_configured`` lets BI segment boot failures by deployment shape; it is
    optional because on the config-fail path ``Settings`` may not exist (the
    caller reads it from the raw env). ``installation_type`` is NOT a param —
    ``_build_event``'s common block stamps it on every event already.

    When ``client`` is passed (config-fail path), use it directly and close
    it after flush; the singleton route would re-hit the underlying
    ``Settings()`` failure and drop the event.
    """
    props: dict[str, str] = {"phase": phase, "error_kind": error_kind}
    if exception_type:
        props["exception_type"] = exception_type
    if transport:
        props["transport"] = transport
    if oauth_configured:
        props["oauth_configured"] = oauth_configured
    try:
        target: AnalyticsClient = client if client is not None else get_analytics()
        target.track_event(EVENT_STARTUP_ERROR, props)
        # Synchronous drain: without this the daemon worker thread is killed
        # by SystemExit / process unwind before httpx finishes the POST.
        target.flush(deadline_s=_STARTUP_ERROR_FLUSH_DEADLINE_S)
    except BaseException:
        # track_event MUST NEVER mask the real startup failure — swallow any
        # analytics-side problem (including SystemExit from a broken flush)
        # and let the ORIGINAL exception propagate to the caller.
        logger.debug("startup_error emit failed", exc_info=True)


def _emit_server_shutdown(
    *, reason: str, started_monotonic: float, lifecycle_source: str = "main"
) -> None:
    """Fire ``opik_mcp_server_shutdown`` and synchronously drain the queue.

    Same drain pattern as ``_emit_startup_error`` — the daemon worker
    thread is killed by SystemExit / process unwind before httpx finishes
    the POST, so we block briefly to make sure the event lands.
    """
    try:
        elapsed = time.monotonic() - started_monotonic
        track_event(
            EVENT_SERVER_SHUTDOWN,
            {
                "reason": reason,
                "lifespan_seconds_bucket": bucket_seconds(elapsed),
                "first_rpc_received": str(transport_probe.first_rpc_received()).lower(),
                "session_reached": str(transport_probe.session_reached()).lower(),
                "lifecycle_source": lifecycle_source,
            },
        )
        get_analytics().flush(deadline_s=_SHUTDOWN_FLUSH_DEADLINE_S)
    except BaseException:
        # Shutdown emit MUST NEVER mask the real exit reason — swallow any
        # analytics-side failure (network, queue, settings) and let the
        # process unwind normally. BaseException covers SystemExit raised
        # by a misbehaving flush implementation; we never want a probe
        # event to escalate process exit.
        logger.debug("server_shutdown emit failed", exc_info=True)


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as e:
        # Settings construction failed — typically a bad COMET_WORKSPACE_ID
        # UUID or an unrecognised OPIK_MCP_AUTO_APPROVE literal. The singleton
        # route would re-construct Settings and re-raise; use a dedicated
        # fallback client that doesn't depend on the broken config so the
        # event still lands. Sentry isn't informed here — the values that
        # drive validation are hardcoded in the deploy pipeline, so this
        # path is dev/test territory and the BI bucket is sufficient.
        fallback = _build_fallback_analytics_client()
        try:
            _emit_startup_error(
                phase="config",
                error_kind="invalid_config",
                exception_type=type(e).__name__,
                client=fallback,
                # Settings construction failed → no installation_type; read
                # oauth_configured straight from the env so the bucket survives.
                oauth_configured=boot_props.oauth_configured_from_env(),
            )
        finally:
            fallback.close()
        raise

    _configure_logging(settings.opik_mcp_log_level)
    transport = settings.opik_mcp_transport.lower()

    # Initialize Sentry BEFORE the first track_event / any user code path
    # that might raise. No-op when OPIK_MCP_SENTRY_ENABLED=false; see
    # error_tracking.py.
    error_tracking.setup_sentry(settings)

    # Claim lifecycle-event ownership before build_app() can run, so the
    # build_app() Starlette lifespan (same process, or inherited by --reload
    # workers) skips its own emit and we never double-count a boot.
    boot_props.mark_lifecycle_owned_by_main()

    # Collect fingerprint BEFORE capturing the monotonic anchor. The
    # fingerprint calls out to subprocess + lsof on macOS (parent-process
    # bucketing) which can add tens-to-hundreds of milliseconds; counting
    # that toward lifespan_seconds_bucket would push real-but-tiny sessions
    # out of the "<5s" probe bucket and obscure the dark-cohort signal.
    fingerprint_props = collect_environment_fingerprint()
    started_monotonic = time.monotonic()

    track_event(
        EVENT_SERVER_STARTED,
        {
            "transport": transport,
            "analytics_enabled": str(settings.opik_mcp_analytics_enabled).lower(),
            "has_workspace": str(settings.comet_workspace is not None).lower(),
            "has_api_key": str(settings.opik_api_key is not None).lower(),
            "has_default_project": str(settings.opik_default_project_name is not None).lower(),
            "install_id_freshly_generated": str(install_id_was_freshly_generated()).lower(),
            "lifecycle_source": "main",
            # Settings-derived auth/transport props. Spread here (caller tier)
            # so the settings-derived auth_mode wins over _build_event's
            # contextvar fallback (which is "none" at boot — no request yet).
            **collect_boot_props(settings),
            **fingerprint_props,
        },
    )

    try:
        _run_transport(settings, transport)
    except SystemExit:
        # Deliberate exit (sys.exit(...) inside _run_transport, e.g. the
        # insecure-token guard). Any startup_error was already emitted at
        # the decision point, but BI also needs the matching shutdown event
        # to close the start/stop funnel — emit it before re-raising.
        _emit_server_shutdown(reason="sys_exit", started_monotonic=started_monotonic)
        raise
    except KeyboardInterrupt:
        # User-initiated stop (SIGINT / Ctrl-C). Not a crash, so we do NOT
        # emit startup_error — just record the shutdown reason and re-raise
        # so the process exits with the standard SIGINT exit code.
        _emit_server_shutdown(reason="keyboard_interrupt", started_monotonic=started_monotonic)
        raise
    except BaseException as e:
        bucketed_type = _bucket_transport_exception_type(e)
        _emit_startup_error(
            phase="transport_start",
            error_kind="transport_crash",
            exception_type=bucketed_type,
            transport=transport,
            oauth_configured=boot_props.oauth_configured(settings),
        )
        # A transport crash is exactly the kind of failure Sentry exists for.
        # Mirror the analytics props so Sentry events have the same shape as
        # tool-call captures: ``phase``/``error_kind``/``exception_type`` as
        # filterable tags, ``startup`` as the transaction (groups alongside
        # ``read``/``write``/... in the issue list), and a fingerprint that
        # splits transport_crash from any future startup buckets.
        if isinstance(e, Exception):
            error_tracking.capture_exception(
                e,
                tags={
                    "phase": "transport_start",
                    "error_kind": "transport_crash",
                    "exception_type": bucketed_type,
                    "transport": transport,
                },
                transaction="startup",
                fingerprint=["{{ default }}", "startup", "transport_crash"],
            )
        _emit_server_shutdown(reason="transport_error", started_monotonic=started_monotonic)
        raise
    else:
        _emit_server_shutdown(reason="clean_exit", started_monotonic=started_monotonic)


def _run_transport(settings: Settings, transport: str) -> None:
    if transport == "stdio":
        # Default: Claude Code (or any MCP client) launches this process and
        # speaks MCP over stdin/stdout. No port, no inbound auth, no uvicorn —
        # whoever can spawn the process already owns its stdio.
        from opik_mcp.analytics.wrappers import install_tools_listed_emitter
        from opik_mcp.server import mcp

        install_tools_listed_emitter(mcp)
        logger.info("startup transport=stdio")
        mcp.run(transport="stdio")
        return

    logger.info(
        "startup transport=http host=%s port=%s reload=%s",
        settings.opik_mcp_host,
        settings.opik_mcp_port,
        settings.opik_mcp_reload,
    )

    # OAuth on HTTP transport needs an explicit resource URI. RFC 9728 makes
    # `resource` REQUIRED in the protected-resource doc, and the AS validates
    # the authorize `resource` param by exact-equality against its own
    # MCP_OAUTH_RESOURCE_URI — so a missing value yields a non-compliant doc and
    # a host-derived fallback that fails every authorize with invalid_target.
    # Fail fast. (No AS configured = API-key-only mode; resource URI N/A.)
    if settings.opik_mcp_as_url and not settings.opik_mcp_resource_uri:
        logger.error(
            "Refusing to start: OPIK_MCP_AS_URL=%r enables OAuth but "
            "OPIK_MCP_RESOURCE_URI is unset. Set it to the exact resource URI the "
            "Authorization Server is configured with (its MCP_OAUTH_RESOURCE_URI, "
            "default <issuer>/api/v1/mcp) — the two must match byte-for-byte or "
            "every /authorize fails with invalid_target.",
            settings.opik_mcp_as_url,
        )
        _emit_startup_error(
            phase="config",
            error_kind="invalid_config",
            transport=transport,
            oauth_configured=boot_props.oauth_configured(settings),
        )
        sys.exit(1)

    # Imported lazily so stdio mode doesn't pay the Starlette import cost.
    from opik_mcp.server import build_app

    # Surface bind failures to our error handler; see _preflight_bind_check.
    _preflight_bind_check(settings.opik_mcp_host, settings.opik_mcp_port)

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

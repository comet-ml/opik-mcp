"""Decorator that emits `opik_mcp_tool_called` on every wrapped tool invocation.

Also lazily emits `opik_mcp_session_initialized` the first time it sees a given
`ctx.session` — Phase-1 substitute for a real `initialize` SDK hook.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from weakref import WeakSet

import anyio
from mcp.types import ListToolsRequest

from opik_mcp import error_tracking
from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    get_analytics,
    transport_probe,
)
from opik_mcp.analytics.errors import (
    bucket_exception,
    derive_http_status,
    unwrap_to_real_cause,
)
from opik_mcp.analytics.events import EVENT_TOOLS_LISTED, bucket_count
from opik_mcp.analytics.mcp_client_info import call_context_props, collect_session_props
from opik_mcp.comet_client import OllieNotEnabledError
from opik_mcp.config import MissingConfigError

logger = logging.getLogger("opik_mcp.analytics.wrappers")

T = TypeVar("T")


# Sentry skip-list: buckets representing user-input or user-config problems.
# These are surfaced to the host LLM as a tool error and counted in BI under
# their explicit ``error_kind``; Sentry only carries the kinds that need a
# human stack trace (server errors, network failures, contract drifts,
# unexpected bugs).
#
# Lives next to the wrapper's capture site rather than inside
# ``error_tracking.py`` because every Sentry capture in this codebase is
# one of ours — deciding *not* to capture is cleaner than capturing then
# dropping server-side in ``before_send``.
_USER_SIDE_ERROR_KINDS: frozenset[str] = frozenset(
    {
        "auth",  # 401 — bad API key / wrong workspace
        "permission",  # 403 — workspace access denied
        "validation",  # 400/422 — payload rejected by Opik or pydantic
        "not_found",  # 404 — entity doesn't exist
    }
)


# User-config problems Sentry shouldn't carry. ``OllieNotEnabledError`` buckets
# to ``"unknown"`` (indistinguishable from real bugs like ``CometProtocolError``
# at the kind level), so it NEEDS this class-based skip. ``MissingConfigError``
# now buckets to ``"validation"`` and is already skipped via
# ``_USER_SIDE_ERROR_KINDS``; it stays here as belt-and-braces so a future
# re-classification can't silently start paging Sentry.
_USER_SIDE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    MissingConfigError,
    OllieNotEnabledError,
)


# Indirection so tests can patch the singleton.
def _client() -> Any:
    return get_analytics()


# Per-process set of sessions we've already announced. WeakSet so dead
# sessions get garbage-collected and don't leak memory across long uptime.
# _seen_session_ids is a TEST-ONLY fallback for objects that don't support
# weak references (e.g. types.SimpleNamespace in our tests): on Python
# 3.13 `WeakSet.add(SimpleNamespace())` raises TypeError. Production
# fastmcp ``ServerSession`` instances always support ``__weakref__`` so the
# WeakSet path is the hot path and dead sessions are reclaimed promptly.
# NOTE: ``id()`` values can be reused after deallocation, so this fallback
# is unsuitable for long-lived production use — but by construction it
# never fires there.
_seen_sessions: WeakSet[Any] = WeakSet()
_seen_session_ids: set[int] = set()


def _reset_seen_sessions_for_tests() -> None:
    """Drop the seen-sessions cache. Test-only — never call from production."""
    _seen_sessions.clear()
    _seen_session_ids.clear()


def _session_is_seen(session: Any) -> bool:
    try:
        return session in _seen_sessions
    except TypeError:
        return id(session) in _seen_session_ids


def _session_mark_seen(session: Any) -> None:
    try:
        _seen_sessions.add(session)
    except TypeError:
        _seen_session_ids.add(id(session))


def _maybe_emit_session_initialized(kwargs: dict[str, Any]) -> None:
    ctx = kwargs.get("ctx")
    if ctx is None:
        return
    session = getattr(ctx, "session", None)
    if session is None or _session_is_seen(session):
        return
    _session_mark_seen(session)
    # A wrapped tool call running proves both: transport delivered an RPC
    # AND the host completed the initialize handshake. Flip both flags so
    # server_shutdown can distinguish probes from real-but-stalled clients.
    transport_probe.mark_first_rpc()
    transport_probe.mark_session_reached()

    try:
        _client().track_event(EVENT_SESSION_INITIALIZED, collect_session_props(session))
    except BaseException:
        # Telemetry MUST NEVER tear down a live MCP session; swallow even
        # BaseException (SystemExit from a misbehaving sink, KeyboardInterrupt
        # racing the emit) so the in-flight tool call continues normally.
        logger.debug("session_initialized emit failed", exc_info=True)


# PRIVACY CONTRACT: a `props_fn` MUST return only low-cardinality, bucketed
# values — never user-supplied free text (queries, names, ids, prose).
# Concrete implementations live alongside each tool in `server.py`; see
# `_write_props`, `_read_props`, `_list_props` for the bucketing pattern
# (`is_batch`, `id_kind`, `had_name_filter`, …). The privacy guarantee is
# enforced end-to-end by `tests/test_analytics_privacy.py`.
PropsFn = Callable[[Any, dict[str, Any]], dict[str, str]]


def _report_to_sentry(
    exc: BaseException,
    *,
    tool_name: str,
    error_kind: str,
    cause_type: str | None,
    duration_ms: int,
    props_fn: PropsFn | None,
    kwargs: dict[str, Any],
) -> None:
    """Capture a tool-call failure with the bucket context BI already tracks.

    ``props_fn`` is invoked with ``result=None`` on the failure path — every
    current implementation derives its bucket props from ``kwargs`` only
    (the ``result`` argument is underscore-prefixed in all six sites). If
    that contract ever changes, the inner try/except swallows the failure
    so we still get the bare exception instead of nothing.

    MCP host fingerprint (``mcp_host`` / ``mcp_client_version``) is attached
    when available — invaluable when triaging which client (Claude Code,
    Cursor, custom) hit a given Sentry issue.

    ``cause_type`` mirrors the BI prop — when the raise site wraps a real
    upstream class via ``ToolError`` / ``OllieStreamError``, the leaf class
    is what Sentry triage actually cares about, so we tag both.
    """
    tags: dict[str, str] = {"tool_name": tool_name, "error_kind": error_kind}
    if cause_type:
        tags["cause_type"] = cause_type
    extras: dict[str, Any] = {"duration_ms": duration_ms}
    if props_fn is not None:
        try:
            tags.update(props_fn(None, kwargs))
        except Exception:
            logger.debug("props_fn raised during sentry context; skipping", exc_info=True)
    _attach_mcp_client_tags(kwargs, tags)
    # transaction = which tool failed (visible in Sentry's issue listing).
    # fingerprint = default stacktrace grouping + tool_name so a shared
    # helper raising the same exception from two tools splits into two
    # issues instead of merging into one.
    error_tracking.capture_exception(
        exc,
        tags=tags,
        extras=extras,
        transaction=tool_name,
        fingerprint=["{{ default }}", tool_name],
    )


def _attach_mcp_client_tags(kwargs: dict[str, Any], tags: dict[str, str]) -> None:
    """Stamp Sentry tags with the SAME bucketed host/version BI uses.

    Reuses ``collect_session_props`` so a host stamping
    ``"acme-internal-wrapper-<user>"`` collapses to ``"other"`` on both
    channels — drift between BI and Sentry would re-introduce the privacy
    leak ``classify_mcp_host`` exists to prevent. Skips emit when there's
    no live ``clientInfo`` so we don't tag every event with the
    ``"other"``/``"unknown"`` defaults.
    """
    ctx = kwargs.get("ctx")
    session = getattr(ctx, "session", None) if ctx is not None else None
    if session is None:
        return
    params = getattr(session, "client_params", None)
    client_info = getattr(params, "clientInfo", None) if params is not None else None
    if client_info is None:
        return
    props = collect_session_props(session)
    tags["mcp_host"] = props["mcp_host"]
    tags["mcp_client_version"] = props["mcp_client_version"]


def instrument_tool(
    name: str,
    *,
    props_fn: PropsFn | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Wrap an async MCP tool handler so every call emits `opik_mcp_tool_called`."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            _maybe_emit_session_initialized(kwargs)
            t0 = time.monotonic()
            error_kind: str | None = None
            exception_type: str | None = None
            cause_type: str | None = None
            http_status: int | None = None
            result: T | None = None
            completed = False
            try:
                result = await fn(*args, **kwargs)
                completed = True
                return result
            except BaseException as exc:
                # Stash class + status BEFORE re-raising so the finally
                # block can emit them. We never read ``exc.args`` / ``str(exc)``
                # — only the class and (for typed exceptions) the canonical
                # HTTP status — keeping the privacy contract intact.
                exception_type = type(exc).__name__
                if isinstance(exc, anyio.get_cancelled_exc_class()):
                    # Host-initiated cancellation: surface as a distinct kind
                    # so dashboards can distinguish cancellations from errors.
                    error_kind = "cancelled"
                elif isinstance(exc, Exception):
                    # Unwrap pure-envelope wrappers (ToolError, OllieStreamError)
                    # so the bucket reflects the real cause. ``exception_type``
                    # keeps the wrapper class (where in our code the failure
                    # surfaced); ``cause_type`` carries the leaf (what actually
                    # broke upstream). Both emit only when distinct.
                    real = unwrap_to_real_cause(exc)
                    if real is not exc:
                        cause_type = type(real).__name__
                    error_kind = bucket_exception(exc)
                    http_status = derive_http_status(exc)
                    # Sentry carries only the kinds worth a human stack trace.
                    # Skip the user-side buckets + the few user-config classes
                    # that collapse into ``"unknown"`` (the coarse bucket can't
                    # distinguish them from real bugs like ``CometProtocolError``).
                    # The class-level skip-list runs against ``real`` so a
                    # ToolError wrapping ``MissingConfigError`` is recognized
                    # as user-side and not paged to Sentry.
                    if error_kind not in _USER_SIDE_ERROR_KINDS and not isinstance(
                        real, _USER_SIDE_EXCEPTIONS
                    ):
                        _report_to_sentry(
                            exc,
                            tool_name=name,
                            error_kind=error_kind,
                            cause_type=cause_type,
                            duration_ms=int((time.monotonic() - t0) * 1000),
                            props_fn=props_fn,
                            kwargs=kwargs,
                        )
                raise
            finally:
                props: dict[str, str] = {
                    "tool_name": name,
                    "success": "true" if completed else "false",
                    "duration_ms": str(int((time.monotonic() - t0) * 1000)),
                }
                # Stamp the bucketed session context (env cohort + MCP host) so
                # BI can segment this event without joining to server_started /
                # session_initialized on install_id. session may be None.
                ctx = kwargs.get("ctx")
                session = getattr(ctx, "session", None) if ctx is not None else None
                props.update(call_context_props(session))
                if error_kind:
                    props["error_kind"] = error_kind
                if exception_type:
                    props["exception_type"] = exception_type
                if cause_type:
                    props["cause_type"] = cause_type
                if http_status is not None:
                    props["http_status"] = str(http_status)
                if props_fn is not None and completed:
                    try:
                        props.update(props_fn(result, kwargs))
                    except Exception:
                        logger.debug("props_fn raised; skipping extras", exc_info=True)
                try:
                    _client().track_event(EVENT_TOOL_CALLED, props)
                except Exception:
                    logger.debug("tool_called emit failed", exc_info=True)

        return wrapper

    return decorator


# Per-process dedup for tools_listed. WeakSet keyed by the request context's
# session when available; otherwise a process-global single-shot fallback.
# Same shape as _seen_sessions for _maybe_emit_session_initialized.
_seen_tools_listed_sessions: WeakSet[Any] = WeakSet()
_tools_listed_fired_processwide: bool = False


def _reset_seen_tools_listed_for_tests() -> None:
    """Drop the dedup state. Test-only — never call from production."""
    global _tools_listed_fired_processwide
    _seen_tools_listed_sessions.clear()
    _tools_listed_fired_processwide = False


def _maybe_emit_tools_listed(result: Any) -> None:
    """Emit tools_listed once per session (or once per process if no session
    is available in the request context).

    Counts the tools in ``result`` and buckets via ``bucket_count``. ``result``
    is the FastMCP ``ListToolsResult`` — ``.root.tools`` is the canonical
    structure; we walk defensively because the lowlevel handler may pass back
    a plain ``ServerResult`` envelope.
    """
    global _tools_listed_fired_processwide

    session = None
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get()
        session = getattr(ctx, "session", None)
    except (ImportError, LookupError, AttributeError):
        session = None

    if session is not None:
        try:
            if session in _seen_tools_listed_sessions:
                return
            _seen_tools_listed_sessions.add(session)
        except TypeError:
            # Session not weak-referenceable (e.g. SimpleNamespace in tests).
            # Fall through to process-wide one-shot.
            if _tools_listed_fired_processwide:
                return
            _tools_listed_fired_processwide = True
    else:
        if _tools_listed_fired_processwide:
            return
        _tools_listed_fired_processwide = True

    tools: list[Any] = []
    inner = getattr(result, "root", None) or result
    candidate = getattr(inner, "tools", None)
    if isinstance(candidate, list):
        tools = candidate

    transport_probe.mark_first_rpc()

    props = {"tool_count_bucket": bucket_count(len(tools))}
    try:
        _client().track_event(EVENT_TOOLS_LISTED, props)
    except BaseException:
        # Same contract as session_initialized: a telemetry-side failure must
        # never propagate out of the request handler and break tools/list.
        logger.debug("tools_listed emit failed", exc_info=True)


def install_tools_listed_emitter(mcp: Any) -> None:
    """Replace the registered ListToolsRequest handler on a FastMCP instance.

    FastMCP wires its request handlers in ``_setup_handlers`` during
    construction, exposing them on ``mcp._mcp_server.request_handlers``.
    There's no decorator slot for ``tools/list``, so we swap the live handler
    in place — preserving its return value and firing tools_listed on every
    successful call. Per-session dedup happens inside _maybe_emit_tools_listed.
    """
    try:
        lowlevel = mcp._mcp_server
    except AttributeError:
        logger.debug("install_tools_listed_emitter: mcp has no _mcp_server attribute")
        return
    original = lowlevel.request_handlers.get(ListToolsRequest)
    if original is None:
        logger.debug("install_tools_listed_emitter: no ListToolsRequest handler registered")
        return

    async def wrapped(req: Any) -> Any:
        result = await original(req)
        try:
            _maybe_emit_tools_listed(result)
        except BaseException:
            # Belt-and-braces: _maybe_emit_tools_listed already swallows its
            # own emit errors, but anything raised by the dedup bookkeeping
            # (WeakSet membership, request_ctx lookup) must NEVER replace the
            # successful tools/list result on its way back to the client.
            logger.debug("tools_listed wrapper raised", exc_info=True)
        return result

    lowlevel.request_handlers[ListToolsRequest] = wrapped

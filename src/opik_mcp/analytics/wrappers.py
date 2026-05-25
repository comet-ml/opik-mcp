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

from opik_mcp import error_tracking
from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    get_analytics,
)
from opik_mcp.analytics.errors import bucket_exception, derive_http_status
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


# A few exceptions bucket to ``"unknown"`` (alongside real bugs like
# ``CometProtocolError`` and ``OllieStreamError``) but actually represent
# user-config problems Sentry shouldn't carry. Class-based skip because
# the coarse ``"unknown"`` bucket can't distinguish them from the bugs.
_USER_SIDE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    MissingConfigError,
    OllieNotEnabledError,
)


# Indirection so tests can patch the singleton.
def _client() -> Any:
    return get_analytics()


# Per-process set of session ids we've already announced. WeakSet so dead
# sessions get garbage-collected and don't leak memory across long uptime.
_seen_sessions: WeakSet[Any] = WeakSet()


def _reset_seen_sessions_for_tests() -> None:
    """Drop the seen-sessions cache. Test-only — never call from production."""
    _seen_sessions.clear()


def _maybe_emit_session_initialized(kwargs: dict[str, Any]) -> None:
    ctx = kwargs.get("ctx")
    if ctx is None:
        return
    session = getattr(ctx, "session", None)
    if session is None or session in _seen_sessions:
        return
    _seen_sessions.add(session)
    params = getattr(session, "client_params", None)
    client_info = getattr(params, "clientInfo", None) if params is not None else None
    props: dict[str, str] = {
        "mcp_host": getattr(client_info, "name", "") or "",
        "mcp_client_version": getattr(client_info, "version", "") or "",
        "mcp_protocol_version": (getattr(params, "protocolVersion", "") or "" if params else ""),
    }
    try:
        _client().track_event(EVENT_SESSION_INITIALIZED, props)
    except Exception:
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
    """
    tags: dict[str, str] = {"tool_name": tool_name, "error_kind": error_kind}
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
    ctx = kwargs.get("ctx")
    session = getattr(ctx, "session", None) if ctx is not None else None
    params = getattr(session, "client_params", None) if session is not None else None
    client_info = getattr(params, "clientInfo", None) if params is not None else None
    if client_info is None:
        return
    host = getattr(client_info, "name", "") or ""
    version = getattr(client_info, "version", "") or ""
    if host:
        tags["mcp_host"] = host
    if version:
        tags["mcp_client_version"] = version


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
                    error_kind = bucket_exception(exc)
                    http_status = derive_http_status(exc)
                    # Sentry carries only the kinds worth a human stack trace.
                    # Skip the user-side buckets + the few user-config classes
                    # that collapse into ``"unknown"`` (the coarse bucket can't
                    # distinguish them from real bugs like ``CometProtocolError``).
                    if error_kind not in _USER_SIDE_ERROR_KINDS and not isinstance(
                        exc, _USER_SIDE_EXCEPTIONS
                    ):
                        _report_to_sentry(
                            exc,
                            tool_name=name,
                            error_kind=error_kind,
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
                if error_kind:
                    props["error_kind"] = error_kind
                if exception_type:
                    props["exception_type"] = exception_type
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

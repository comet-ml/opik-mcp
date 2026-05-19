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

from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    get_analytics,
)
from opik_mcp.comet_client import (
    CometAuthError,
    CometProtocolError,
    OllieNotEnabledError,
)
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)

logger = logging.getLogger("opik_mcp.analytics.wrappers")

T = TypeVar("T")

_ERROR_KIND_TABLE: tuple[tuple[type[BaseException], str], ...] = (
    (MissingConfigError, "missing_config"),
    (CometAuthError, "comet_auth_failed"),
    (OllieNotEnabledError, "ollie_not_enabled"),
    (CometProtocolError, "comet_protocol_error"),
    (OpikAuthError, "opik_http_4xx"),
    (OpikNotFoundError, "opik_http_4xx"),
    (OpikValidationError, "opik_http_4xx"),
    (OpikServerError, "opik_http_5xx"),
    (PodNotReadyError, "pod_warmup_timeout"),
    (OllieAuthError, "ollie_auth_failed"),
    (OllieStreamError, "ollie_stream_error"),
)


def _classify(exc: BaseException) -> str:
    for cls, kind in _ERROR_KIND_TABLE:
        if isinstance(exc, cls):
            return kind
    return "unknown"


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
            result: T | None = None
            completed = False
            try:
                result = await fn(*args, **kwargs)
                completed = True
                return result
            except BaseException as exc:
                if isinstance(exc, anyio.get_cancelled_exc_class()):
                    # Host-initiated cancellation: surface as a distinct kind
                    # so dashboards can distinguish cancellations from errors.
                    error_kind = "cancelled"
                elif isinstance(exc, Exception):
                    error_kind = _classify(exc)
                raise
            finally:
                props: dict[str, str] = {
                    "tool_name": name,
                    "success": "true" if completed else "false",
                    "duration_ms": str(int((time.monotonic() - t0) * 1000)),
                }
                if error_kind:
                    props["error_kind"] = error_kind
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

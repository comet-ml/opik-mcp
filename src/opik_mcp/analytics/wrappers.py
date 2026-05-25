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
import httpx
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    get_analytics,
    transport_probe,
)
from opik_mcp.comet_client import (
    CometAuthError,
    CometPermissionError,
    CometProtocolError,
    OllieNotEnabledError,
)
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)

logger = logging.getLogger("opik_mcp.analytics.wrappers")

T = TypeVar("T")

# Linear walk: first matching row wins, so list subclasses BEFORE their parents
# (OpikPermissionError before OpikAuthError, CometPermissionError before
# CometAuthError). The httpx network errors are listed *after* the typed-API
# errors so an authenticated 401 isn't mis-bucketed as a network failure (an
# OpikAuthError instance is itself not an httpx exception, but ordering keeps
# the contract explicit if anyone later adds a hybrid type).
#
# PRIVACY: the *classifier* (``_classify`` below) keys off exception class
# only — never ``exc.args`` / ``exc.message`` — so adding a row here is
# privacy-neutral. This guarantee covers ONLY the ``error_kind`` field. The
# exception messages themselves DO carry user data (entity ids, workspace
# names, ~200 chars of response body via ``_error_detail``); they are safe
# *because* nothing here serializes them. Anyone adding a future field like
# ``error_detail`` MUST bucket / hash / drop the source string — never
# ``str(exc)``.
_ERROR_KIND_TABLE: tuple[tuple[type[BaseException], str], ...] = (
    (MissingConfigError, "missing_config"),
    # Comet — subclass first
    (CometPermissionError, "comet_permission_denied"),
    (CometAuthError, "comet_auth_failed"),
    (OllieNotEnabledError, "ollie_not_enabled"),
    (CometProtocolError, "comet_protocol_error"),
    # Opik HTTP — split by status, subclass first
    (OpikPermissionError, "opik_permission_denied"),
    (OpikAuthError, "opik_auth_failed"),
    (OpikNotFoundError, "opik_not_found"),
    (OpikValidationError, "opik_validation_failed"),
    (OpikServerError, "opik_http_5xx"),
    # Ollie streaming
    (PodNotReadyError, "pod_warmup_timeout"),
    (OllieAuthError, "ollie_auth_failed"),
    (OllieStreamError, "ollie_stream_error"),
    # Pydantic validation from INSIDE the tool body — e.g.
    # ``RunExperimentConfig.model_validate(experiment_config)`` in
    # ``server.run_experiment`` or ``op.pydantic_model.model_validate(data)``
    # in the write dispatcher. NOTE: FastMCP's ``Tool.run`` validates the
    # tool's outer signature BEFORE calling our wrapped function and converts
    # the resulting ``ValidationError`` into a ``ToolError``, so the very
    # outermost arg-coercion failure never hits this branch. The bucket only
    # fires when a tool itself calls ``model_validate`` on a sub-payload.
    (PydanticValidationError, "tool_args_invalid"),
    # Network — httpx.RequestError is the common base for ConnectError,
    # TimeoutException (read/connect/write/pool), ReadError, etc. Catches the
    # bulk of what used to land in "unknown" on flaky networks. HTTPStatusError
    # is intentionally NOT here: when we use it, the typed Opik/Comet wrappers
    # have already classified the status — a raw HTTPStatusError reaching this
    # layer is a bug, not a network failure.
    (httpx.RequestError, "network_error"),
)


def _classify(exc: BaseException) -> str:
    for cls, kind in _ERROR_KIND_TABLE:
        if isinstance(exc, cls):
            return kind
    return "unknown"


# Indirection so tests can patch the singleton.
def _client() -> Any:
    return get_analytics()


# Known MCP host allowlist. Prefix match (`startswith`) on the lowercased
# clientInfo.name → bucket. Anything else → "other". Privacy contract:
# clientInfo.name is host-controlled string; passing it through raw would
# uncap cardinality and risk re-identifying users via per-install names
# (e.g. "acme-internal-wrapper-<user>"). Order matters: list more-specific
# prefixes BEFORE shorter ones that they would match (e.g. roo BEFORE cline
# so "roo-cline" → "roo", not "cline").
_MCP_HOST_PATTERNS: tuple[tuple[str, str], ...] = (
    ("claude-desktop", "claude-desktop"),
    ("claude-code", "claude-code"),
    ("cursor", "cursor"),
    ("roo", "roo"),
    ("cline", "cline"),
    ("continue", "continue"),
    ("windsurf", "windsurf"),
    ("mcp-inspector", "mcp-inspector"),
)


def _classify_mcp_host(raw: str) -> str:
    needle = (raw or "").strip().lower()
    if not needle:
        return "other"
    for pattern, bucket in _MCP_HOST_PATTERNS:
        if needle.startswith(pattern):
            return bucket
    return "other"


# host_llm_family is DERIVED from the bucketed host name so we never
# branch on raw input.  Cursor promoted to its own family (paying-user
# host worth separating); mcp-inspector tagged so probe traffic is
# distinguishable from real installs.
_HOST_LLM_FAMILY: dict[str, str] = {
    "claude-desktop": "anthropic",
    "claude-code": "anthropic",
    "cursor": "cursor",
    "cline": "mixed",
    "continue": "mixed",
    "roo": "mixed",
    "windsurf": "mixed",
    "mcp-inspector": "inspector",
}


def _classify_host_llm_family(mcp_host_bucket: str) -> str:
    return _HOST_LLM_FAMILY.get(mcp_host_bucket, "unknown")


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

    params = getattr(session, "client_params", None)
    client_info = getattr(params, "clientInfo", None) if params is not None else None
    capabilities = getattr(params, "capabilities", None) if params is not None else None
    raw_host = getattr(client_info, "name", "") or ""
    mcp_host_bucket = _classify_mcp_host(raw_host)

    props: dict[str, str] = {
        "mcp_host": mcp_host_bucket,
        "mcp_client_version": getattr(client_info, "version", "") or "",
        "mcp_protocol_version": (getattr(params, "protocolVersion", "") or "" if params else ""),
        "host_llm_family": _classify_host_llm_family(mcp_host_bucket),
        "caps_sampling": str(getattr(capabilities, "sampling", None) is not None).lower(),
        "caps_elicitation": str(getattr(capabilities, "elicitation", None) is not None).lower(),
        "caps_roots": str(getattr(capabilities, "roots", None) is not None).lower(),
        "caps_tasks": str(getattr(capabilities, "tasks", None) is not None).lower(),
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

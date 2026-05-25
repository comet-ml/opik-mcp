"""Decorator that emits `opik_mcp_tool_called` on every wrapped tool invocation.

Also lazily emits `opik_mcp_session_initialized` the first time it sees a given
`ctx.session` — Phase-1 substitute for a real `initialize` SDK hook.
"""

from __future__ import annotations

import functools
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from weakref import WeakSet

import anyio
from mcp.types import ListToolsRequest

from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    get_analytics,
    transport_probe,
)
from opik_mcp.analytics.errors import bucket_exception, derive_http_status
from opik_mcp.analytics.events import EVENT_TOOLS_LISTED, bucket_count

logger = logging.getLogger("opik_mcp.analytics.wrappers")

T = TypeVar("T")


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


# Privacy: clientInfo.version and protocolVersion are host-controlled strings.
# A host could stamp anything in there — a build hash with a username substring,
# a per-install token, a path. We allow ONLY shapes that match a public versioning
# convention and bucket everything else to "unknown". Length cap is a belt-and-
# braces guard so an attacker can't sneak past the regex with a 200-char value
# that happens to start with digits.
_SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?(-[a-zA-Z0-9.-]+)?$")
_PROTOCOL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION_MAX_LEN = 32


def _bucket_mcp_client_version(raw: str | None) -> str:
    """Return ``raw`` if it matches a semver shape, else ``"unknown"``.

    MCP host clients (Claude Desktop, Cursor, Cline, Continue, …) stamp their
    own version into ``clientInfo.version``. Allowlisting the semver shape
    preserves the analytical signal (which version of the host is connecting)
    while refusing arbitrary strings that could carry PII.
    """
    if not raw or len(raw) > _VERSION_MAX_LEN:
        return "unknown"
    return raw if _SEMVER_RE.match(raw) else "unknown"


def _bucket_mcp_protocol_version(raw: str | None) -> str:
    """Return ``raw`` if it matches the MCP ``YYYY-MM-DD`` date shape, else ``"unknown"``.

    The MCP spec uses date-stamped protocol versions (e.g. ``"2025-06-01"``).
    Anything else is either an unknown future format or host-supplied garbage,
    so we collapse it to ``"unknown"`` rather than letting it widen cardinality.
    """
    if not raw or len(raw) > _VERSION_MAX_LEN:
        return "unknown"
    return raw if _PROTOCOL_DATE_RE.match(raw) else "unknown"


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

    raw_client_version = getattr(client_info, "version", "") or ""
    raw_protocol_version = (getattr(params, "protocolVersion", "") or "") if params else ""
    props: dict[str, str] = {
        "mcp_host": mcp_host_bucket,
        "mcp_client_version": _bucket_mcp_client_version(raw_client_version),
        "mcp_protocol_version": _bucket_mcp_protocol_version(raw_protocol_version),
        "host_llm_family": _classify_host_llm_family(mcp_host_bucket),
        "caps_sampling": str(getattr(capabilities, "sampling", None) is not None).lower(),
        "caps_elicitation": str(getattr(capabilities, "elicitation", None) is not None).lower(),
        "caps_roots": str(getattr(capabilities, "roots", None) is not None).lower(),
        "caps_tasks": str(getattr(capabilities, "tasks", None) is not None).lower(),
    }
    try:
        _client().track_event(EVENT_SESSION_INITIALIZED, props)
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

"""Shared error taxonomy for analytics events.

Both ``opik_mcp_tool_called`` and ``opik_mcp_ask_ollie_completed`` emit an
``error_kind`` drawn from the ``ErrorKind`` allowlist (see
``opik_mcp.error_kinds``). The granular exception class is preserved as
``exception_type`` — coarse bucketing for "what should we fix next?"
dashboards, granular class names for follow-up investigation.

Classification model: each of our typed exception classes carries
``error_kind: ClassVar[ErrorKind]`` and ``http_status: ClassVar[int | None]``
as ClassVars. The classifier reads those attributes via ``getattr`` — no
``isinstance`` cascade. Python's MRO does the work, so subclasses like
``OpikPermissionError`` automatically shadow the parent's bucket.

We still need special-case branches for:
- pure-envelope wrappers (``ToolError``, ``OllieStreamError`` when chained):
  the unwrap walks past them to find the real cause first.
- non-controllable classes we can't put attributes on:
  ``httpx.HTTPStatusError`` (status comes from the response), other
  ``httpx`` network errors, and ``pydantic.ValidationError``.

PRIVACY: public functions never read ``exc.args`` / ``str(exc)`` / response
body. Classification is class-level (ClassVar reads) plus a tightly-scoped
whitelist of integer-only instance fields: ``httpx.Response.status_code``
and ``BackendError.extra["backend_error"]["status"]``. The contract is
machine-checked by ``tests/test_analytics_privacy.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.error_kinds import ErrorKind
from opik_mcp.ollie_client import OllieStreamError
from opik_mcp.writes.errors import BackendError

# Re-export so existing call sites (and downstream readers) keep their
# ``from opik_mcp.analytics.errors import ErrorKind`` import working.
__all__ = [
    "ErrorKind",
    "bucket_exception",
    "bucket_http_status",
    "derive_http_status",
    "unwrap_to_real_cause",
]


# Pure-envelope exception classes — these wrap a real upstream cause via
# ``raise X from e`` (or implicit ``__context__``) and never carry their own
# bucketing signal beyond ``"unknown"``. ``bucket_exception`` walks past
# them to find the real culprit; emit sites preserve the wrapper class name
# in ``exception_type`` and the unwrapped class in ``cause_type``.
#
# Why ``ToolError``: FastMCP's contract is that tool handlers surface failures
# to the host via ``ToolError`` (see ``read_list/``, ``writes/``). Without the
# unwrap, every read/list/write failure showed up as ``unknown / ToolError``
# in BI, masking auth/not_found/upstream_5xx patterns.
#
# Why ``OllieStreamError``: a ``RuntimeError`` subclass we raise both as a
# leaf (e.g. protocol-drift "no session_id") AND as a wrapper around upstream
# HTTP failures. Its own ``error_kind`` ClassVar is ``"unknown"`` so the
# bare-leaf case still buckets correctly; the wrapper case routes by cause.
_WRAPPER_CLASSES: tuple[type[BaseException], ...] = (
    ToolError,
    OllieStreamError,
)


# Cap on chain depth to keep cycles / deeply-nested wrappers from turning the
# classifier into a runtime hazard. 4 hops is well past anything our codebase
# produces (the deepest natural chain is ToolError ← OllieStreamError ← real,
# 3 hops) and any longer chain almost certainly indicates a protocol bug
# rather than a meaningful bucket-by-leaf signal.
_UNWRAP_MAX_DEPTH = 4


def unwrap_to_real_cause(
    exc: BaseException, *, max_depth: int = _UNWRAP_MAX_DEPTH
) -> BaseException:
    """Walk ``__cause__`` / ``__context__`` through pure-envelope wrapper
    exceptions and return the innermost non-wrapper, or ``exc`` if no unwrap
    applies.

    Preference order (matches Python's traceback display):
    1. ``__cause__`` if set — the explicit ``raise X from e`` chain.
    2. ``__context__`` if ``__suppress_context__`` is False — the implicit
       chain Python sets when raising inside an ``except`` block.
    3. Stop — the wrapper has no recoverable cause; treat it as the leaf.

    The walk also stops at the first non-wrapper exception (a meaningful leaf)
    and at chain cycles (defensive — ``__cause__`` can be set to anything).

    PRIVACY: inspects ``type(...)``, ``__cause__``, ``__context__``,
    ``__suppress_context__`` only — never reads ``args`` / ``str(exc)``.
    """
    seen: set[int] = {id(exc)}
    current: BaseException = exc
    for _ in range(max_depth):
        if not isinstance(current, _WRAPPER_CLASSES):
            return current
        nxt: BaseException | None = current.__cause__
        if nxt is None and not current.__suppress_context__:
            nxt = current.__context__
        if nxt is None or id(nxt) in seen:
            return current
        seen.add(id(nxt))
        current = nxt
    return current


def _class_attr(exc: BaseException, name: str) -> Any:
    """Return ``type(exc).<name>`` if defined as a class-level attribute on
    one of our typed exception classes, else ``None``.

    We deliberately do NOT read instance attributes — only class-level ones
    set as ``ClassVar``. Avoids accidentally treating a stray instance attr
    (potentially smuggled in from user-controlled data on some future class)
    as a taxonomy signal.
    (Instance reads are handled separately by ``_instance_http_status``.)
    """
    value = getattr(type(exc), name, None)
    return value


def _instance_http_status(real: BaseException) -> int | None:
    """Return the upstream HTTP status for classes that carry it on the
    instance, else ``None``.

    Two classes need this instance-level read:
    - ``httpx.HTTPStatusError``: status on ``.response.status_code``. Third
      party, can't add a ClassVar.
    - ``BackendError`` (write tool): status on ``.extra["backend_error"]
      ["status"]``. Ours, but each instance wraps a different upstream
      status — a ClassVar would lock the bucket to one value.

    Treating them as the same pattern lets ``bucket_exception`` route both
    via the same arm before any ClassVar lookup.

    PRIVACY: integer-only instance reads, parallel to one another. No
    message text or response body is inspected.
    """
    if isinstance(real, httpx.HTTPStatusError):
        return real.response.status_code
    if isinstance(real, BackendError):
        status = real.extra.get("backend_error", {}).get("status")
        if isinstance(status, int):
            return status
    return None


def derive_http_status(exc: BaseException) -> int | None:
    """Return the canonical HTTP status for a typed exception, else ``None``.

    Resolution order:
    1. Unwrap pure-envelope wrappers (``ToolError`` / ``OllieStreamError``)
       to the real cause.
    2. Try the instance-level status (``_instance_http_status``) — httpx
       responses and ``BackendError.extra`` both vary per call.
    3. Fall back to ``type(real).http_status`` (the ClassVar each typed
       Opik/Comet/Ollie/Write exception declares).
    """
    real = unwrap_to_real_cause(exc)
    instance_status = _instance_http_status(real)
    if instance_status is not None:
        return instance_status
    status = _class_attr(real, "http_status")
    return status if isinstance(status, int) else None


def bucket_http_status(status: int) -> ErrorKind:
    """Map an HTTP status code to the coarse ``ErrorKind`` allowlist.

    Used when an emitter has a status code but no exception (e.g. a future
    write tool that surfaces a 422 via return value rather than raise).
    """
    if status == 401:
        return "auth"
    if status == 403:
        return "permission"
    if status == 404:
        return "not_found"
    if status in (408, 504):
        return "timeout"
    if 400 <= status < 500:
        return "validation"
    if 500 <= status < 600:
        return "upstream_5xx"
    return "unknown"


def _bucket_external(real: BaseException) -> ErrorKind | None:
    """Bucket external classes we can't annotate with ClassVars.

    Status-bearing classes (``httpx.HTTPStatusError``, ``BackendError``) are
    handled earlier in ``bucket_exception`` via ``_instance_http_status``; by
    the time control reaches here, ``real`` is a non-status external class.
    """
    if isinstance(real, PydanticValidationError):
        return "validation"
    # ``httpx.TimeoutException`` is the base for connect/read/write/pool
    # timeouts. Keep it BEFORE the broader ``RequestError`` arm so a
    # ``ReadTimeout`` lands in ``timeout``, not ``network``.
    if isinstance(real, httpx.TimeoutException):
        return "timeout"
    if isinstance(real, httpx.RequestError):
        return "network"
    return None


def bucket_exception(exc: BaseException, http_status: int | None = None) -> ErrorKind:
    """Bucket an exception into the coarse ``ErrorKind`` allowlist.

    Resolution order:
    1. Unwrap pure-envelope wrappers (``ToolError`` / ``OllieStreamError``)
       to the real upstream cause.
    2. Instance-level status (``_instance_http_status``) — httpx responses
       and ``BackendError`` carry the status on instance state that varies
       per call; this arm runs BEFORE the ClassVar lookup so the per-call
       status wins over any class-level fallback.
    3. Class-level taxonomy (``type(real).error_kind``) — every typed
       Opik/Comet/Ollie/Write exception declares this ClassVar.
    4. Special-case external hierarchies we can't annotate (``pydantic``,
       non-status ``httpx`` errors).
    5. Caller-supplied ``http_status`` (lets a future tool surface a 422-
       style validation error via return value rather than raise).
    6. Fall through to ``"unknown"``.

    PRIVACY: never reads ``exc.args`` / ``str(exc)``. The ClassVar read is
    class-level; the instance-status read is integer-only and the unwrap
    inspects ``__cause__`` / ``__context__`` references, not their messages.
    """
    real = unwrap_to_real_cause(exc)
    instance_status = _instance_http_status(real)
    if instance_status is not None:
        return bucket_http_status(instance_status)
    kind = _class_attr(real, "error_kind")
    if isinstance(kind, str):
        return kind  # type: ignore[return-value]
    external = _bucket_external(real)
    if external is not None:
        return external
    if http_status is not None:
        return bucket_http_status(http_status)
    return "unknown"

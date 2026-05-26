"""Shared error taxonomy for analytics events.

Both ``opik_mcp_tool_called`` and ``opik_mcp_ask_ollie_completed`` emit an
``error_kind`` drawn from the ``ErrorKind`` allowlist below. The granular
exception class is preserved as ``exception_type`` — coarse bucketing for
"what should we fix next?" dashboards, granular class names for follow-up
investigation.

PRIVACY: every public function in this module keys off the exception CLASS
only — never on ``exc.args`` / ``str(exc)`` / response body. That guarantees
no exception message ever surfaces in an analytics property. The contract is
machine-checked by ``tests/test_analytics_privacy.py``.
"""

from __future__ import annotations

from typing import Literal

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError as PydanticValidationError

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

# The receiver only ever sees one of these values. Adding a new bucket is a
# BI schema change — extend cautiously and keep the Literal in sync.
ErrorKind = Literal[
    "auth",
    "validation",
    "not_found",
    "permission",
    "timeout",
    "network",
    "upstream_5xx",
    "cancelled",
    "unknown",
]


# Pure-envelope exception classes — these wrap a real upstream cause via
# ``raise X from e`` (or implicit ``__context__``) and never carry their own
# bucketing signal. ``bucket_exception`` walks past them to find the real
# culprit; emit sites preserve the wrapper class name in ``exception_type``
# and the unwrapped class in ``cause_type``.
#
# Why ``ToolError``: FastMCP's contract is that tool handlers surface failures
# to the host via ``ToolError`` (see ``read_list/``, ``writes/``). Without the
# unwrap, every read/list/write failure showed up as ``unknown / ToolError``
# in BI, masking auth/not_found/upstream_5xx patterns.
#
# Why ``OllieStreamError``: a ``RuntimeError`` subclass we raise both as a
# leaf (e.g. protocol-drift "no session_id") AND as a wrapper around upstream
# HTTP failures (``ollie_client.py:161`` raises it from a 404; ``ask_ollie.py``
# raises it from pod ``error`` SSE frames carrying ``upstream_code``). The
# leaf case still falls through to ``"unknown"`` below; the wrapper case now
# routes by its real cause.
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


# Canonical HTTP status for each typed Opik/Comet exception. Our client
# layers raise these in place of carrying ``status_code`` on the exception
# instance, so we recover the status here from the class itself. Sub-
# classes are intentionally listed BEFORE their parents (``OpikPermissionError``
# extends ``OpikAuthError``) so ``isinstance`` walks resolve to the more-
# specific status first.
_EXC_TO_HTTP_STATUS: tuple[tuple[type[BaseException], int], ...] = (
    (OpikPermissionError, 403),
    (OpikAuthError, 401),
    (OpikNotFoundError, 404),
    (OpikValidationError, 400),
    (OpikServerError, 500),
    (CometPermissionError, 403),
    (CometAuthError, 401),
)


def derive_http_status(exc: BaseException) -> int | None:
    """Return the canonical HTTP status for a typed exception, else ``None``.

    Designed for ``opik_mcp_tool_called`` / ``ask_ollie_completed`` emit
    sites: the BI receiver needs the status alongside ``error_kind`` so a
    dashboard can split, e.g., ``auth`` failures into 401 vs 403 without
    losing the coarse bucket.

    Unwraps through pure-envelope wrappers (``ToolError``, ``OllieStreamError``)
    so a ``ToolError`` carrying an ``OpikAuthError`` still resolves to 401.
    """
    real = unwrap_to_real_cause(exc)
    # ``httpx.HTTPStatusError`` carries the real status on the response.
    # Other httpx network errors (ConnectError, TimeoutException, …) have
    # no status — they failed before getting one.
    if isinstance(real, httpx.HTTPStatusError):
        return real.response.status_code
    for cls, status in _EXC_TO_HTTP_STATUS:
        if isinstance(real, cls):
            return status
    return None


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


def bucket_exception(exc: BaseException, http_status: int | None = None) -> ErrorKind:
    """Bucket an exception into the coarse ``ErrorKind`` allowlist.

    ``http_status`` lets callers supersede the class-based mapping when they
    have a more specific signal (e.g. an ``httpx.HTTPStatusError`` they
    already inspected). When omitted, the function derives a status from
    typed Opik/Comet/Ollie exceptions via ``derive_http_status``.

    Unwraps through pure-envelope wrappers (``ToolError``, ``OllieStreamError``)
    so the bucket reflects the real upstream cause. A wrapper with no cause
    (or whose cause chain ends in a non-meaningful class) falls through to
    ``"unknown"`` — same as before this unwrap was added.

    PRIVACY: never reads ``exc.args`` / ``str(exc)``. Class-only — the unwrap
    inspects ``__cause__`` / ``__context__`` references, not their messages.
    """
    real = unwrap_to_real_cause(exc)
    # 1) Permission BEFORE auth — OpikPermissionError extends OpikAuthError,
    #    so a 403 must not be miscategorized as "auth" (which means 401).
    if isinstance(real, (OpikPermissionError, CometPermissionError)):
        return "permission"
    if isinstance(real, (OpikAuthError, CometAuthError, OllieAuthError)):
        return "auth"
    if isinstance(real, OpikNotFoundError):
        return "not_found"
    if isinstance(real, (OpikValidationError, PydanticValidationError)):
        return "validation"
    if isinstance(real, OpikServerError):
        return "upstream_5xx"
    if isinstance(real, PodNotReadyError):
        return "timeout"
    # ``httpx.TimeoutException`` is the base for connect/read/write/pool
    # timeouts. Keep it BEFORE the broader ``RequestError`` arm so a
    # ``ReadTimeout`` lands in ``timeout``, not ``network``.
    if isinstance(real, httpx.TimeoutException):
        return "timeout"
    if isinstance(real, httpx.HTTPStatusError):
        return bucket_http_status(real.response.status_code)
    if isinstance(real, httpx.RequestError):
        return "network"
    # MissingConfigError, OllieStreamError, OllieNotEnabledError, CometProtocolError
    # don't map cleanly to the receiver's taxonomy — they're our own
    # control-flow errors, not upstream failures. Caller-supplied status
    # takes precedence when available.
    if http_status is not None:
        return bucket_http_status(http_status)
    if isinstance(
        real, (OllieStreamError, OllieNotEnabledError, CometProtocolError, MissingConfigError)
    ):
        return "unknown"
    return "unknown"

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
    """
    # ``httpx.HTTPStatusError`` carries the real status on the response.
    # Other httpx network errors (ConnectError, TimeoutException, …) have
    # no status — they failed before getting one.
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    for cls, status in _EXC_TO_HTTP_STATUS:
        if isinstance(exc, cls):
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

    PRIVACY: never reads ``exc.args`` / ``str(exc)``. Class-only.
    """
    # 1) Permission BEFORE auth — OpikPermissionError extends OpikAuthError,
    #    so a 403 must not be miscategorized as "auth" (which means 401).
    if isinstance(exc, (OpikPermissionError, CometPermissionError)):
        return "permission"
    if isinstance(exc, (OpikAuthError, CometAuthError, OllieAuthError)):
        return "auth"
    if isinstance(exc, OpikNotFoundError):
        return "not_found"
    if isinstance(exc, (OpikValidationError, PydanticValidationError)):
        return "validation"
    if isinstance(exc, OpikServerError):
        return "upstream_5xx"
    if isinstance(exc, PodNotReadyError):
        return "timeout"
    # ``httpx.TimeoutException`` is the base for connect/read/write/pool
    # timeouts. Keep it BEFORE the broader ``RequestError`` arm so a
    # ``ReadTimeout`` lands in ``timeout``, not ``network``.
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return bucket_http_status(exc.response.status_code)
    if isinstance(exc, httpx.RequestError):
        return "network"
    # MissingConfigError, OllieStreamError, OllieNotEnabledError, CometProtocolError
    # don't map cleanly to the receiver's taxonomy — they're our own
    # control-flow errors, not upstream failures. Caller-supplied status
    # takes precedence when available.
    if http_status is not None:
        return bucket_http_status(http_status)
    if isinstance(
        exc, (OllieStreamError, OllieNotEnabledError, CometProtocolError, MissingConfigError)
    ):
        return "unknown"
    return "unknown"

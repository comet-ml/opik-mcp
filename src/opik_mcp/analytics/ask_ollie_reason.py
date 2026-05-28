"""Ask-Ollie-specific failure taxonomy.

Layered on top of the coarse ``ErrorKind`` (which is shared across every
tool). ``ask_ollie`` orchestrates four upstream services — comet-backend
(pod discovery), the Ollie pod itself (warmup, session create, SSE
stream, confirm POST) — and the same exception class can surface in more
than one phase with very different operational meanings: an
``OllieAuthError`` from ``wait_ready`` is a stale PPAUTH after discovery
succeeded, while the same class from ``stream_events`` is a mid-stream
auth flip. Coarse ``error_kind=auth`` collapses both; the per-phase
``failure_reason`` separates them so a dashboard can route the right
on-call.

PRIVACY: every value here is a fixed ``Literal`` — no message text or
upstream body is inspected. Phase is set by ask_ollie before each await
boundary; reason is derived from ``(phase, type(exc), http_status,
upstream_code)`` only. ``exc.args`` / ``str(exc)`` are never read.
"""

from __future__ import annotations

from typing import Literal

import httpx

from opik_mcp.analytics.errors import (
    derive_http_status,
    unwrap_to_real_cause,
)
from opik_mcp.comet_client import (
    CometAuthError,
    CometPermissionError,
    CometProtocolError,
    OllieNotAvailableError,
    OllieNotEnabledError,
)
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import (
    ConfirmDeclinedError,
    ConfirmPostError,
    OllieAuthError,
    OllieStreamError,
    PodErrorEventError,
    PodNotReadyError,
    PodSessionCreateError,
    PodSessionLostError,
    PodStreamIdleError,
)

AskOlliePhase = Literal[
    "config",
    "discover",
    "warmup",
    "create_session",
    "stream",
    "confirm",
]

AskOllieFailureReason = Literal[
    # Config phase
    "missing_config",
    # Pod discovery (comet-backend → /api/opik/ollie/compute-api-key)
    "comet_auth",
    "workspace_forbidden",
    "ollie_not_available",
    "ollie_not_enabled",
    "comet_protocol_drift",
    "comet_5xx",
    "comet_4xx_other",
    "comet_timeout",
    "comet_network",
    # Pod (warmup / create_session / stream)
    "pod_warmup_timeout",
    "pod_auth",
    "session_create_protocol_drift",
    "session_lost",
    "pod_error_event",
    "pod_stream_idle",
    "pod_5xx",
    "pod_4xx_other",
    "pod_timeout",
    "pod_network",
    # Confirm flow
    "confirm_declined",
    "confirm_post_failed",
    # Fall-through
    "unknown",
]


def _is_5xx(status: int | None) -> bool:
    return status is not None and 500 <= status < 600


def _is_4xx(status: int | None) -> bool:
    return status is not None and 400 <= status < 500


def _comet_http_reason(status: int | None) -> AskOllieFailureReason:
    if status == 401:
        return "comet_auth"
    if status == 403:
        return "workspace_forbidden"
    if status == 404:
        return "ollie_not_available"
    if _is_5xx(status):
        return "comet_5xx"
    if _is_4xx(status):
        return "comet_4xx_other"
    return "unknown"


def _pod_http_reason(status: int | None) -> AskOllieFailureReason:
    if status in (401, 403):
        return "pod_auth"
    if _is_5xx(status):
        return "pod_5xx"
    if _is_4xx(status):
        return "pod_4xx_other"
    return "unknown"


def _network_reason(
    real: BaseException, phase: AskOlliePhase
) -> AskOllieFailureReason | None:
    """Map non-status httpx errors to the phase-appropriate bucket."""
    if isinstance(real, httpx.TimeoutException):
        return "comet_timeout" if phase == "discover" else "pod_timeout"
    if isinstance(real, httpx.RequestError):
        return "comet_network" if phase == "discover" else "pod_network"
    return None


# Map of typed exception class → fixed failure_reason. Checked in MRO-aware
# order via isinstance, so subclasses must precede parents in the tuple.
# Pinned per raise site by the typed-exception design (see
# ``ollie_client.py``); the analytics layer routes by ``isinstance`` rather
# than sniffing message text.
_TYPED_REASONS: tuple[tuple[type[BaseException], AskOllieFailureReason], ...] = (
    # Config
    (MissingConfigError, "missing_config"),
    # Comet typed (CometPermissionError subclasses CometAuthError — list first)
    (OllieNotAvailableError, "ollie_not_available"),
    (OllieNotEnabledError, "ollie_not_enabled"),
    (CometProtocolError, "comet_protocol_drift"),
    (CometPermissionError, "workspace_forbidden"),
    (CometAuthError, "comet_auth"),
    # Pod typed
    (PodNotReadyError, "pod_warmup_timeout"),
    (OllieAuthError, "pod_auth"),
    # OllieStreamError subclasses — list ALL before the parent so isinstance
    # routes to the subclass bucket. Parent isn't in this table; bare
    # OllieStreamError falls through to the http/network/unknown arms.
    (PodSessionCreateError, "session_create_protocol_drift"),
    (PodSessionLostError, "session_lost"),
    (PodErrorEventError, "pod_error_event"),
    (PodStreamIdleError, "pod_stream_idle"),
    (ConfirmDeclinedError, "confirm_declined"),
    (ConfirmPostError, "confirm_post_failed"),
)


def derive_failure_reason(
    exc: BaseException,
    phase: AskOlliePhase,
    *,
    upstream_code: str | None = None,  # noqa: ARG001 (reserved for future sub-bucketing)
) -> AskOllieFailureReason:
    """Derive a phase-aware ``failure_reason`` for an ``ask_ollie`` failure.

    Resolution order:
    1. Unwrap ``ToolError`` / ``OllieStreamError`` pure-envelope wrappers
       to the real cause so we route on the leaf class. ``OllieStreamError``
       SUBCLASSES (PodSessionCreateError, PodErrorEventError, etc.) are NOT
       envelopes — they're leaves — and ``unwrap_to_real_cause`` stops at
       them via ``_WRAPPER_CLASSES`` membership (only the bare parent is in
       that tuple). The wrapper-only check + the subclass-first ordering in
       ``_TYPED_REASONS`` are what keep this right.
    2. Match against ``_TYPED_REASONS`` (typed leaf classes pinned at their
       raise sites).
    3. Status-bearing exceptions (``httpx.HTTPStatusError`` etc.): bucket
       by status × phase via ``_comet_http_reason`` / ``_pod_http_reason``.
    4. Non-status network/timeout (``httpx.TimeoutException`` etc.): bucket
       by ``_network_reason``.
    5. ``unknown`` fall-through.

    PRIVACY: inspects class identity, ``derive_http_status`` (integer-only
    instance read on ``httpx.Response``), and the caller-supplied phase.
    Never reads ``exc.args`` / ``str(exc)`` / ``upstream_code`` body.
    """
    real = unwrap_to_real_cause(exc)

    for klass, reason in _TYPED_REASONS:
        if isinstance(real, klass):
            return reason

    # Status-bearing (httpx.HTTPStatusError, BackendError, ClassVar fallback).
    # Note: bare OllieStreamError (no cause unwrapped past it) lands here
    # with no status → falls through to "unknown". That's correct: a
    # bare-parent raise from outside the named raise sites is a
    # protocol-drift signal we don't have a bucket for — show up as
    # ``unknown`` and someone investigates.
    status = derive_http_status(exc)
    if status is not None:
        if phase == "discover":
            return _comet_http_reason(status)
        return _pod_http_reason(status)

    net = _network_reason(real, phase)
    if net is not None:
        return net

    # Suppress unused-import warning for the parent type — kept in scope so
    # readers see at a glance that we intentionally do NOT route bare
    # OllieStreamError through _TYPED_REASONS (only its subclasses do).
    _ = OllieStreamError

    return "unknown"

"""Shared analytics taxonomy.

Lives in a leaf module so any layer can import the ``ErrorKind`` Literal
without creating cycles. The typed exception classes (``opik_client``,
``comet_client``, ``ollie_client``, ``config``) declare their bucket as a
``ClassVar[ErrorKind]``; ``analytics/errors.py`` reads that attribute via
``getattr`` instead of running an ``isinstance`` cascade.

Adding a new bucket is a BI schema change — extend cautiously and update
``docs/analytics.md`` (if present) plus the privacy-test allowlist.
"""

from __future__ import annotations

from typing import Literal

ErrorKind = Literal[
    # Coarse buckets shared by every tool (read / list / write / schema /
    # ask_ollie). HTTP-status-shaped failures from the Opik backend and the
    # generic httpx layer route here.
    "auth",
    "validation",
    "not_found",
    "permission",
    "timeout",
    "network",
    "upstream_5xx",
    "cancelled",
    "unknown",
    # Startup-only bucket emitted by ``__main__._emit_startup_error`` when
    # ``Settings`` construction raises ``pydantic.ValidationError`` (bad
    # COMET_WORKSPACE_ID UUID, unrecognised OPIK_MCP_AUTO_APPROVE literal,
    # etc.). The runtime ``bucket_exception`` never returns this value —
    # listed here so the BI receiver's allowlist covers every emit site.
    "invalid_config",
    # ask_ollie-specific reasons. The ask_ollie pipeline is multi-stage
    # (discover_pod → wait_ready → create_session → stream_events →
    # confirm_session) and each stage has its own failure modes that BI
    # wants to split out without a separate field. Other tools never raise
    # these — but they share the field so a single dashboard column can
    # carry both vocabularies.
    "comet_auth",  # CometAuthError 401 — bad/expired OPIK_API_KEY
    "comet_permission",  # CometPermissionError 403 — workspace access denied
    "comet_protocol",  # CometProtocolError — Comet response shape drift
    "ollie_not_enabled",  # OllieNotEnabledError — workspace lacks ollie-assist
    "pod_not_ready",  # PodNotReadyError — warmup timed out
    "pod_auth",  # OllieAuthError — pod rejected PPAUTH cookie
    "session_create_failed",  # POST /sessions returned no session_id
    "session_evicted",  # stream_events 404 — session GC'd between create + stream
    "confirm_failed",  # POST /sessions/.../confirm raised
    "stream_error_frame",  # pod emitted an SSE `error` event
    "stream_idle",  # heartbeat tripped the idle-timeout watchdog
    "stream_protocol",  # bare OllieStreamError — our own protocol-drift signal
]

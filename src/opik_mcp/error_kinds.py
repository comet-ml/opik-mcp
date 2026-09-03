"""Shared analytics taxonomy.

Lives in a leaf module so any layer can import the ``ErrorKind`` Literal
without creating cycles. The typed exception classes (``opik_client``,
``config``, ``writes/errors``) declare their bucket as a
``ClassVar[ErrorKind]``; ``analytics/errors.py`` reads that attribute via
``getattr`` instead of running an ``isinstance`` cascade.

Adding a new bucket is a BI schema change — extend cautiously and update
``docs/analytics.md`` (if present) plus the privacy-test allowlist.
"""

from __future__ import annotations

from typing import Literal

ErrorKind = Literal[
    # Coarse buckets shared by every tool (read / list / write / schema /
    # read_skill). HTTP-status-shaped failures from the Opik backend and the
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
    # COMET_WORKSPACE_ID UUID, out-of-range numeric override, etc.). The
    # runtime ``bucket_exception`` never returns this value — listed here so
    # the BI receiver's allowlist covers every emit site.
    "invalid_config",
]

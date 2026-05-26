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

"""Single source of truth for bucketing ``ctx.session.client_params``.

The MCP host stamps ``clientInfo.name``, ``clientInfo.version``, and
``protocolVersion`` into the session it opens. Those strings flow into BOTH
BI (the ``opik_mcp_session_initialized`` event) and Sentry (tool-call
capture tags), and the cardinality / privacy contract must be identical
across channels — a host stamping ``"acme-internal-wrapper-<user>"`` should
bucket to ``"other"`` everywhere, not leak through to one sink and be
bucketed in the other.

Centralising the extractor here keeps the two consumers (`wrappers.py`'s
session-initialized emit and the Sentry capture path) in lock-step.
"""

from __future__ import annotations

import re
from typing import Any

# Known MCP host allowlist. Prefix match (``startswith``) on the lower-
# cased ``clientInfo.name`` → bucket. Anything else → "other". Order
# matters: list more-specific prefixes BEFORE shorter ones they would
# match (e.g. roo BEFORE cline so "roo-cline" → "roo", not "cline").
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


def classify_mcp_host(raw: str) -> str:
    needle = (raw or "").strip().lower()
    if not needle:
        return "other"
    for pattern, bucket in _MCP_HOST_PATTERNS:
        if needle.startswith(pattern):
            return bucket
    return "other"


# host_llm_family is DERIVED from the bucketed host name so we never
# branch on raw input. Cursor promoted to its own family (paying-user
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


def classify_host_llm_family(mcp_host_bucket: str) -> str:
    return _HOST_LLM_FAMILY.get(mcp_host_bucket, "unknown")


# Privacy: clientInfo.version and protocolVersion are host-controlled
# strings. A host could stamp anything in there — a build hash with a
# username substring, a per-install token, a path. We allow ONLY shapes
# that match a public versioning convention and bucket everything else
# to "unknown". Length cap is a belt-and-braces guard so an attacker
# can't sneak past the regex with a 200-char value that happens to
# start with digits.
_SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?(-[a-zA-Z0-9.-]+)?$")
_PROTOCOL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION_MAX_LEN = 32


def bucket_mcp_client_version(raw: str | None) -> str:
    """Return ``raw`` if it matches a semver shape, else ``"unknown"``."""
    if not raw or len(raw) > _VERSION_MAX_LEN:
        return "unknown"
    return raw if _SEMVER_RE.match(raw) else "unknown"


def bucket_mcp_protocol_version(raw: str | None) -> str:
    """Return ``raw`` if it matches the MCP ``YYYY-MM-DD`` shape, else ``"unknown"``."""
    if not raw or len(raw) > _VERSION_MAX_LEN:
        return "unknown"
    return raw if _PROTOCOL_DATE_RE.match(raw) else "unknown"


def collect_session_props(session: Any) -> dict[str, str]:
    """Return the bucketed MCP-client property dict for a live session.

    Walks ``session.client_params.clientInfo`` / ``.capabilities`` and
    returns the 8-key dict consumed by BOTH the ``session_initialized``
    BI event and the Sentry capture path.

    Defensive against missing intermediates: a session that hasn't
    completed the initialize handshake (``client_params is None``) still
    returns a valid dict with ``"other"`` / ``"unknown"`` / ``"false"``
    sentinels — callers should never crash because the host raced us.
    """
    params = getattr(session, "client_params", None) if session is not None else None
    client_info = getattr(params, "clientInfo", None) if params is not None else None
    capabilities = getattr(params, "capabilities", None) if params is not None else None

    raw_host = getattr(client_info, "name", "") or ""
    raw_client_version = getattr(client_info, "version", "") or ""
    raw_protocol_version = (getattr(params, "protocolVersion", "") or "") if params else ""
    mcp_host_bucket = classify_mcp_host(raw_host)

    return {
        "mcp_host": mcp_host_bucket,
        "mcp_client_version": bucket_mcp_client_version(raw_client_version),
        "mcp_protocol_version": bucket_mcp_protocol_version(raw_protocol_version),
        "host_llm_family": classify_host_llm_family(mcp_host_bucket),
        "caps_sampling": str(getattr(capabilities, "sampling", None) is not None).lower(),
        "caps_elicitation": str(getattr(capabilities, "elicitation", None) is not None).lower(),
        "caps_roots": str(getattr(capabilities, "roots", None) is not None).lower(),
        "caps_tasks": str(getattr(capabilities, "tasks", None) is not None).lower(),
    }

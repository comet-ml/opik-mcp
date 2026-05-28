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

import contextlib
import re
from typing import Any
from weakref import WeakKeyDictionary

from opik_mcp.analytics.environment import cached_call_context_env

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
    ("zed", "zed"),
    ("vscode", "vscode"),
    ("goose", "goose"),
    ("librechat", "librechat"),
    ("5ire", "5ire"),
    ("opencode", "opencode"),
    ("codex", "codex"),
    ("gemini-cli", "gemini-cli"),
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
    "zed": "mixed",
    "vscode": "mixed",
    "goose": "mixed",
    "librechat": "mixed",
    "5ire": "mixed",
    "opencode": "mixed",
    "codex": "openai",
    "gemini-cli": "google",
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


# Per-session cache of the bucketed host block. The MCP handshake is fixed for
# the life of a session, so ``clientInfo`` is read and bucketed once, then
# reused on every per-call emit — keeps the privacy-sensitive classification
# off the hot path. ``WeakKeyDictionary`` so dead sessions are reclaimed;
# stand-ins that don't support weak references (e.g. ``SimpleNamespace`` in
# tests) skip the cache and recompute, which is cheap and pure.
_session_host_cache: WeakKeyDictionary[Any, dict[str, str]] = WeakKeyDictionary()


def _host_context(session: Any) -> dict[str, str]:
    """The two handshake-derived fields (``mcp_host`` / ``host_llm_family``),
    cached per session.

    Only these two fields are cached — NOT the full 8-key
    ``collect_session_props`` block. A caller that needs the version / caps
    fields too should call ``collect_session_props`` directly (the
    ``session_initialized`` emit does); don't layer it on top of this and
    re-extract twice.
    """
    try:
        cached = _session_host_cache.get(session)
    except TypeError:
        cached = None
    if cached is not None:
        return cached
    props = collect_session_props(session)
    host = {"mcp_host": props["mcp_host"], "host_llm_family": props["host_llm_family"]}
    with contextlib.suppress(TypeError):
        _session_host_cache[session] = host
    return host


def call_context_props(session: Any) -> dict[str, str]:
    """The session-context block stamped on every per-call analytics event.

    Six fields BI uses to segment ``tool_called`` / ``ask_ollie_completed`` by
    real-user cohort and MCP host WITHOUT joining back to ``server_started`` /
    ``session_initialized`` on ``install_id``:

    - ``is_ci`` / ``is_container`` / ``launch_method`` /
      ``install_id_freshly_generated`` — process-stable env (cached once)
    - ``mcp_host`` / ``host_llm_family`` — per-session handshake (cached on
      first read)

    Every value is a boolean string or an allowlisted enum — never a raw path,
    hostname, or ``clientInfo`` string. ``session`` may be ``None`` (no
    handshake yet); the host fields fall back to ``"other"`` / ``"unknown"``.
    """
    return {**cached_call_context_env(), **_host_context(session)}


def _reset_call_context_cache_for_tests() -> None:
    """Drop both context caches. Test-only — never call from production."""
    _session_host_cache.clear()
    cached_call_context_env.cache_clear()

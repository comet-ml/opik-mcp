"""Event-name constants + low-cardinality bucket helpers.

Buckets are deliberate: they give actionable distributions without leaking
identifiable values. Thresholds picked to align with common LLM-context budgets
(~2k / ~8k / ~32k tokens) and to keep `tool_called` properties stringifiable.

# Allowlist enums (privacy contract)

Every analytics property is either a boolean string, a hardcoded-allowlist
string, or a bucketed integer/duration. The allowlists below MUST stay in sync
with the classifiers in ``environment.py`` (launch method / parent process) and
``mcp_client_info.py`` (mcp host / host LLM family) — adding a new bucket is a BI
schema change and requires updating both the classifier and the corresponding
Literal here. Tests that pin the BI shape live in
``tests/test_analytics_events.py``, ``tests/test_analytics_privacy.py`` and
``tests/test_analytics_lifespan.py``.

Each Literal documents the *only* values the receiver will ever see for that
property. Anything outside the allowlist is bucketed to a fallback ("other",
"unknown", "") at the emit site — the receiver never sees raw host input.

Two declared exceptions to "boolean / enum / bucket":

- Pseudonymous identity hashes (``api_key_sha256``, ``token_sha256``) are
  64-char SHA-256 hex digests. Not enums, but safe: irreversible one-way
  transforms of secrets the backend already holds (it joins on the digest).
  The raw key/token NEVER leaves the process. This is enforced by tests that
  call ``client._build_event`` directly
  (``tests/test_analytics_client_build_event.py``); the recorder-based tests in
  ``test_analytics_privacy.py`` intercept at ``track_event`` and never see what
  ``_build_event`` builds, so they cannot catch a leak inside it.
- Workspace fields (``workspace``, ``request_workspace``, ``workspace_id``) are
  emitted as plaintext/UUID — an accepted posture, since the workspace name is
  already used as the top-level ``user_id`` (``resolve_anonymous_id``).

Never emit free-text queries, paths, filenames, or other user prose.
"""

from __future__ import annotations

from typing import Literal

# ``launch_method``: bucketed ``sys.executable`` path. See
# ``environment._LAUNCH_METHOD_PATTERNS``.
LaunchMethod = Literal[
    "uvx",
    "pipx",
    "venv",
    "system",
    "unknown",
]

# ``parent_process``: bucketed parent-process comm name. See
# ``environment._PARENT_PROCESS_PATTERNS``.
ParentProcess = Literal[
    "docker-entrypoint",
    "claude",
    "cursor",
    "vscode",
    "jetbrains",
    "bash",
    "zsh",
    "fish",
    "python",
    "node",
    "sshd",
    "systemd",
    "launchd",
    "other",
]

# ``mcp_host``: bucketed MCP host (clientInfo.name). MUST stay in sync with
# ``mcp_client_info._MCP_HOST_PATTERNS`` — every bucket that classifier can
# emit is declared here (enforced by
# ``test_analytics_events.test_mcp_host_literal_covers_all_classifier_buckets``).
McpHost = Literal[
    "claude-desktop",
    "claude-code",
    "cursor",
    "roo",
    "cline",
    "continue",
    "windsurf",
    "mcp-inspector",
    "zed",
    "vscode",
    "goose",
    "librechat",
    "5ire",
    "opencode",
    "codex",
    "gemini-cli",
    "other",
]

# ``host_llm_family``: derived from the bucketed ``mcp_host``. MUST stay in sync
# with ``mcp_client_info._HOST_LLM_FAMILY`` values (enforced by
# ``test_analytics_events.test_host_llm_family_literal_covers_all_classifier_values``).
HostLlmFamily = Literal[
    "anthropic",
    "cursor",
    "openai",
    "google",
    "mixed",
    "inspector",
    "unknown",
]

# ``reason``: shutdown classification. See ``__main__._emit_server_shutdown``
# call sites in ``main()``.
ShutdownReason = Literal[
    "clean_exit",
    "transport_error",
    "keyboard_interrupt",
    "sys_exit",
]

# ``lifespan_seconds_bucket``: discrete duration buckets. See ``bucket_seconds``
# below — values MUST match the return values of that function.
LifespanSecondsBucket = Literal[
    "<5s",
    "5-60s",
    "1-10m",
    "10-60m",
    "1-24h",
    ">24h",
]

# ``installation_type``: Opik destination class. Mirrors
# ``error_tracking._installation_type`` so opik-mcp and opik dashboards share
# tag values. CRITICAL: the self-hosted value is hyphenated ("self-hosted"),
# never "self_hosted" — BI filters key off the exact string.
InstallationType = Literal["cloud", "self-hosted", "local"]

# ``auth_mode``: how the caller authenticated. At boot it is settings-derived
# (``boot_props.auth_mode_at_boot``); per-request it is derived from the inbound
# bearer in ``client._build_event``.
AuthMode = Literal["oauth", "api_key", "none"]

# ``resource_uri_scheme``: scheme of ``OPIK_MCP_RESOURCE_URI``; "none" when unset.
ResourceUriScheme = Literal["https", "http", "none"]

# ``lifecycle_source`` (on server_started / server_shutdown): which path emitted
# the lifecycle event — ``"main"`` (__main__.main) or ``"lifespan"`` (the
# build_app() Starlette lifespan, the hosted Docker/--factory path). Lets BI
# confirm the hosted fleet is no longer dark for boot events (GAP#1).
LifecycleSource = Literal["main", "lifespan"]


EVENT_SERVER_STARTED = "opik_mcp_server_started"
EVENT_SESSION_INITIALIZED = "opik_mcp_session_initialized"
EVENT_TOOL_CALLED = "opik_mcp_tool_called"
EVENT_ASK_OLLIE_COMPLETED = "opik_mcp_ask_ollie_completed"
EVENT_AUTO_APPROVAL = "opik_mcp_auto_approval"
# Emitted from the startup path when the server fails to come up — settings
# validation crash, refused HTTP bind, or transport.run() exception. Pairs
# with ``opik_mcp_server_started`` to form an install-funnel: started without
# a matching error = healthy boot; either alone signals a problem.
EVENT_STARTUP_ERROR = "opik_mcp_startup_error"
EVENT_TOOLS_LISTED = "opik_mcp_tools_listed"
# Pairs with server_started. Carries handshake-progress flags
# (first_rpc_received, session_reached) and lifespan bucket so BI can
# slice the dark cohort into {pure probe, handshake-failed, healthy-short,
# healthy-long}.
EVENT_SERVER_SHUTDOWN = "opik_mcp_server_shutdown"
# Emitted (HTTP transport only) when an inbound request is rejected before
# reaching a tool: 401 from BearerAuthMiddleware (missing/malformed bearer) or
# 421/403 from the SDK transport-security guard (Host/Origin). The key HTTPS
# health signal — without it auth failures are invisible. See
# ``AuthRejectionMiddleware`` in server.py.
EVENT_AUTH_REJECTED = "opik_mcp_auth_rejected"


def bucket_tokens(n: int) -> str:
    if n < 2_000:
        return "<2k"
    if n < 8_000:
        return "2k-8k"
    if n < 32_000:
        return "8k-32k"
    return ">32k"


def bucket_text_len(s: str | None) -> str:
    n = len(s) if s else 0
    if n < 100:
        return "<100"
    if n < 1000:
        return "100-1000"
    return ">1000"


def bucket_count(n: int) -> str:
    if n == 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 100:
        return "11-100"
    if n <= 1_000:
        return "101-1000"
    return ">1000"


def bucket_seconds(n: float) -> str:
    # <5s isolates probe / crash-loop traffic from "real client connected
    # and disconnected before completing the handshake" (5-60s).
    if n < 5:
        return "<5s"
    if n < 60:
        return "5-60s"
    if n < 600:
        return "1-10m"
    if n < 3600:
        return "10-60m"
    if n < 86400:
        return "1-24h"
    return ">24h"


# ``path_bucket`` (on ``opik_mcp_auth_rejected``): coarse request-path class.
# Never carries the raw path — the receiver only ever sees these four buckets.
# ``"other"`` covers OAuth-flow proxy paths (/authorize, /register, /token, …)
# and any unknown path. Those proxy paths are unauthenticated pass-throughs, so
# in practice auth-rejection events carry ``"mcp"`` (our resource-server bearer
# rejection) or the Host/Origin-guard rejections; ``"other"`` is mostly stray
# probe traffic.
PathBucket = Literal["mcp", "health", "well_known", "other"]


def bucket_path(path: str, mcp_http_path: str = "/mcp") -> str:
    """Bucket a request path to a low-cardinality enum. Never emits the raw path.

    ``mcp_http_path`` is the configured MCP transport mount (OPIK_MCP_HTTP_PATH);
    a request to it (or a subpath) buckets to ``"mcp"``, so the bucketing stays
    correct when an operator remaps the endpoint behind a path-prefix proxy.

    Matching is exact-or-subpath (``== mount`` or ``mount + "/"`` prefix) rather
    than a bare ``startswith`` so a sibling like ``/mcpfoo`` or ``/healthz`` does
    not get mis-bucketed as the real endpoint.
    """
    p = path or ""
    if p.startswith("/.well-known/"):
        return "well_known"
    if p == "/health" or p.startswith("/health/"):
        return "health"
    mount = mcp_http_path.rstrip("/")
    if p == mount or p.startswith(mount + "/"):
        return "mcp"
    return "other"

"""Event-name constants + low-cardinality bucket helpers.

Buckets are deliberate: they give actionable distributions without leaking
identifiable values. Thresholds picked to align with common LLM-context budgets
(~2k / ~8k / ~32k tokens) and to keep `tool_called` properties stringifiable.

# Allowlist enums (privacy contract)

Every analytics property is either a boolean string, a hardcoded-allowlist
string, or a bucketed integer/duration. The allowlists below MUST stay in sync
with the classifiers in ``environment.py`` (launch method / parent process),
``mcp_client_info.py`` (mcp host / host LLM family) and ``analytics/client.py``
(``_resolve_workspace`` / ``_resolve_user``) — adding a new bucket is a BI
schema change and requires updating both the classifier and the corresponding
Literal here. Tests that pin the BI shape live in
``tests/test_analytics_events.py``, ``tests/test_analytics_privacy.py``,
``tests/test_analytics_lifespan.py`` and ``tests/test_analytics_client.py``
(the last asserts against the decoded request body actually posted).

Each Literal documents the *only* values the receiver will ever see for that
property. Anything outside the allowlist is bucketed to a fallback ("other",
"unknown", "") at the emit site — the receiver never sees raw host input.

Three declared exceptions to "boolean / enum / bucket":

- Pseudonymous identity hashes (``api_key_sha256``, ``token_sha256``,
  ``mcp_session_sha256``, ``env_id_sha256``) are 64-char SHA-256 hex digests. Not enums, but safe:
  irreversible one-way transforms of values the backend already holds. The raw
  key/token/session-id NEVER leaves the process. This is enforced by tests that
  call ``client._build_event`` directly
  (``tests/test_analytics_client_build_event.py``); the recorder-based tests in
  ``test_analytics_privacy.py`` intercept at ``track_event`` and never see what
  ``_build_event`` builds, so they cannot catch a leak inside it.

  ``mcp_session_sha256`` is a SESSION grain and is **not** the adoption funnel's
  key. A session ends, so it cannot answer retention ("active on 3+ distinct
  days") any more than the token could. The funnel needs the Comet login —
  ``user_id`` with ``user_id_kind='comet_user'`` — which ``caller_identity``
  already resolves and which is live on stdio today; hosted reads zero only
  because it runs 0.2.12, predating that work, so the fix there is a deploy.

  What the session digest does buy is narrower. The OAuth access token lives ONE
  HOUR, and a handshake recurs on every mint while a tool call does not, so
  token-keyed ratios fell as usage rose — 533 of 568 tokens died inside the TTL
  and invoked at 9.6%, against ~80% for the 35 that outlived it. A client keeps
  its ``Mcp-Session-Id`` across refreshes, so the session digest counts an
  8-hour session ONCE, and it still groups events when ``lookup_identity``
  misses (pod restart, LRU eviction) instead of collapsing into the nil
  ``install_id``. Hashed rather than raw because possession of a session id plus
  a token addresses a live session, which makes it bearer-equivalent.

  ``env_id_sha256`` is the digest of the OS machine id (see ``EnvIdKind``). It
  contains NO user-derived data — no hostname, no OS username — so it identifies
  a machine and nothing about a person, and it is hashed anyway because a raw
  machine id is a stable device identifier we have no reason to hold.
- Workspace fields (``workspace``, ``request_workspace``, ``workspace_id``) are
  emitted as plaintext/UUID — an accepted posture: the workspace name is a
  tenant label, not a person, and BI cannot attribute usage without it.
- **Caller identity** (top-level ``user_id``) is the caller's Comet login, in
  plaintext. This is a deliberate amendment to the original "identity only as a
  digest" rule, agreed with BI, and it is the ONLY personal identifier emitted.
  Three reasons it is sanctioned rather than hashed:

  1. It is what the rest of the product already sends. The Opik frontend
     identifies users to Segment, PostHog and Reo.Dev with this same plaintext
     login; opik-mcp was the outlier.
  2. The warehouse's canonical user key *is* that login. A digest would be
     unjoinable, recreating the dead end already demonstrated by
     ``api_key_sha256``, for which no key→user mapping exists anywhere.
  3. It travels to Comet's own analytics endpoint — a service that already
     holds the value.

  ``user_id_kind`` declares which sort of identifier the field holds, so a
  reader never has to infer it. Widening identity does NOT widen anything else:
  that the login appears ONLY as ``user_id`` and never bleeds into
  ``event_properties`` is pinned in ``tests/test_analytics_client_build_event.py``
  (the recorder-based suite cannot see the common block — see its docstring).

Never emit free-text queries, paths, filenames, or other user prose.
"""

from __future__ import annotations

from dataclasses import dataclass
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

# ``parent_process``: bucketed comm name of the IMMEDIATE parent process. See
# ``environment._PARENT_PROCESS_PATTERNS``.
#
# FROZEN. Semantics and value set are unchanged since first release, and must
# stay that way — dashboards and trends are built on these exact buckets. Its
# two known blind spots are NOT fixed here, on purpose:
#   - under ``uvx`` (our recommended install path) the parent is the uv runner,
#     so this reports "other" and the real host is invisible;
#   - on Windows it always reports "other", because its reader is POSIX-only.
# Both are addressed by the additive ``host_process`` field below.
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

# ``host_process`` (NEW): the nearest ancestor that identifies WHO launched us.
# Same bucket vocabulary as ``ParentProcess`` plus "uv", and unlike that field it
# steps over a package runner to reach the host behind it and works on Windows.
# "uv" appears when the runner was detected but the grandparent could not be
# read (notably Windows, which has no dependency-free ppid lookup).
#
# This is the field new reporting should use for host attribution.
HostProcess = Literal[
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
    "uv",
    "other",
]

# ``env_id_kind`` (NEW): where the machine identity came from. "machine" means a
# real OS machine id was read (/etc/machine-id, macOS IOPlatformUUID, Windows
# MachineGuid) and ``env_id_sha256`` carries its digest; "unknown" means none was
# readable and NO digest is emitted.
#
# Why it exists alongside ``install_id``: that field is a UUID in a file under
# HOME, so a reinstall mints a brand-new identity (inflating "new installs") and
# an unwritable HOME collapses it to the nil sentinel, merging every such
# deployment into one row. A machine id survives both. It is also the only
# identity available to local / self-hosted users, who run with auth disabled and
# so can never resolve a username — ~18k successful tool calls across ~36
# installs in a 30-day window.
#
# Machine-scoped by design: the digest deliberately excludes the OS username, so
# two people sharing a box merge and no user-derived data enters the hash. A
# hostname fallback was considered and rejected — in a container the hostname is
# the container id, so it would churn per run while looking authoritative.
EnvIdKind = Literal["machine", "unknown"]

# ``launcher`` (NEW): the package runner that spawned this process, when there
# was one. Emitted alongside ``host_process`` so the uvx install path stays
# countable after ``host_process`` folds it away. "unknown" is the
# detector-raised fallback (``_safe``), not a real observation.
Launcher = Literal["uv", "none", "unknown"]

# ``mcp_host``: bucketed MCP host (clientInfo.name). MUST stay in sync with
# ``mcp_client_info._MCP_HOST_PATTERNS`` — every bucket that classifier can
# emit is declared here (enforced by
# ``test_analytics_events.test_mcp_host_literal_covers_all_classifier_buckets``).
# FROZEN. An absent ``clientInfo.name`` reports "other" here, the same bucket as
# an unrecognised one — a known wart, kept because existing dashboards count on
# it. ``McpClient`` below separates the two.
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

# ``mcp_client`` (NEW): canonical client bucket, and what new reporting should
# use. Two differences from the frozen ``McpHost``:
#
#   - It recognises the names clients actually send. ``claude-desktop`` was a
#     DEAD bucket under the old classifier — zero events in a 30-day fleet
#     window — because Claude Desktop identifies itself as "claude-ai"; likewise
#     VS Code sends "Visual Studio Code", which never matched a "vscode" prefix.
#   - "absent" (no/empty clientInfo) is separated from "other" (a real client we
#     have no pattern for). Merging them is what made the largest cohort in the
#     fleet unreadable: an instrumentation gap looked identical to genuine
#     long-tail client diversity.
McpClient = Literal[
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
    "absent",
]

# ``host_llm_family`` / ``client_llm_family``: derived from the bucketed
# ``mcp_host`` and ``mcp_client`` respectively, through the SAME mapping — so a
# bucket that is unmapped (including ``mcp_client``'s "absent") falls to
# "unknown". MUST stay in sync with ``mcp_client_info._HOST_LLM_FAMILY`` values
# (enforced by
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
# ``config.installation_type`` (shared with error_tracking's Sentry tag) so
# opik-mcp and opik dashboards share tag values. CRITICAL: the self-hosted value
# is hyphenated ("self-hosted"), never "self_hosted" — BI keys off the exact string.
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

# ``user_id_kind``: what the top-level ``user_id`` actually holds. The classifier
# is ``client._build_event``. BI counts real users with
# ``WHERE user_id_kind = 'comet_user'``; the field's ABSENCE marks events emitted
# before identity resolution shipped, when ``user_id`` was a workspace name
# falling back to an install id.
UserIdKind = Literal["comet_user", "install_id"]

# ``install_id_kind``: whether ``install_id`` is a real on-disk id ("file") or the
# nil-UUID sentinel used when HOME is unwritable ("fallback"). The classifier is
# ``identity.install_id_kind``.
#
# CRITICAL for BI, because the sentinel is a SHARED identity rather than a missing
# one: every deployment with an unwritable HOME reports the SAME ``install_id``,
# so unrelated installs merge into one counted row. On the hosted server that is
# 100% of events — hundreds of people behind a single "install". Any tile that
# counts installs, or attributes one host/workspace per install, must exclude
# ``install_id_kind = 'fallback'`` or it is silently reporting an aggregate as an
# individual. ``install_id`` itself is unchanged, so no existing tile moves until
# it opts in.
InstallIdKind = Literal["file", "fallback"]

# ``identity_lookup``: why ``user_id_kind`` came out the way it did. Exists because
# "anonymous" has two causes that must not be added together, and which were
# previously indistinguishable:
#
# - "resolved"        — an identity was found; ``user_id`` is a Comet login.
# - "none_expected"   — no inbound credential, so anonymity is CORRECT, not a
#                       fault. Local and self-hosted Opik run with auth disabled
#                       by design; ~60% of all tool calls are this.
# - "miss"            — a credential WAS presented and we still could not resolve
#                       it. This is a DEFECT, and it is the one number that says
#                       whether hosted identity is actually working. Causes:
#                       the handshake introspection failed, the pod restarted and
#                       emptied the in-memory store, the credential was evicted,
#                       or the credential is an API key forwarded to a hosted
#                       server, which ``caller_identity`` cannot resolve at all.
#
# Without this split, a hosted server resolving nothing looks identical to a
# laptop running open-source Opik: both report ``user_id_kind='install_id'``. That
# is precisely why the hosted deploy could not be verified from the data.
IdentityLookup = Literal["resolved", "none_expected", "miss"]

# ``workspace_kind``: where the reported ``workspace`` name came from. The
# classifier is ``client._resolve_workspace``. CRITICAL for BI: "placeholder" is
# the literal "default" on an install that resolved nothing, and it collides with
# a real cloud workspace of that name — those rows must never be name-joined.
#
# "unknown" carries no ``workspace`` value at all: nothing was configured and
# nothing resolved. It exists so this field is stamped on every event that
# carries ``user_id_kind``, which is what lets BI total workspace_kind without
# an unstamped remainder silently going missing.
# "template" is an operator-configured value that was never filled in — a config
# snippet pasted verbatim. It is reported rather than hidden: the value itself
# says which snippet failed the user (`${input:…}` is VS Code, `<your-workspace>`
# is our README), and the kind keeps it out of workspace joins.
WorkspaceKind = Literal["resolved", "configured", "placeholder", "template", "unknown"]


@dataclass(frozen=True, slots=True)
class Attributed[K: str]:
    """A value emitted alongside the discriminator that says where it came from.

    Both identity fields in the common block have this shape — a string BI reads,
    plus a Literal saying how to interpret it — and neither is safe to read
    without the other: a login and an install id are both strings, and a resolved
    workspace and the placeholder are both names. Pairing them in one type is
    what stops an emit site stamping the value and forgetting the label.
    """

    value: str
    kind: K


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


# ``rejection_reason`` (on ``opik_mcp_auth_rejected``): why a request was
# rejected before reaching a tool. 401 shapes from BearerAuthMiddleware
# (missing_header / not_bearer / empty_token) + the SDK transport-security
# guard's 421 (host) / 403 (origin). Derived from status + header SHAPE only —
# never the token value.
AuthRejectionReason = Literal[
    "missing_header",
    "not_bearer",
    "empty_token",
    "token_rejected",
    "host_rejected",
    "origin_rejected",
]

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

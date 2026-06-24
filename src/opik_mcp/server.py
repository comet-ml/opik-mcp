import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from opik_mcp.analytics import (
    EVENT_AUTH_REJECTED,
    EVENT_SERVER_SHUTDOWN,
    EVENT_SERVER_STARTED,
    boot_props,
    get_analytics,
    track_event,
)
from opik_mcp.analytics.environment import cached_call_context_env, collect_environment_fingerprint
from opik_mcp.analytics.events import bucket_count, bucket_path
from opik_mcp.analytics.wrappers import install_tools_listed_emitter, instrument_tool
from opik_mcp.ask_ollie import AskOllieResult, run_ask_ollie
from opik_mcp.auth_context import (
    classify_bearer,
    inbound_authorization,
    inbound_workspace,
    resolved_workspace_name,
    settings_auth_mode,
)
from opik_mcp.config import MissingConfigError, Settings, get_settings
from opik_mcp.instructions import render_instructions
from opik_mcp.oauth_identity import resolve_workspace_name
from opik_mcp.opik_client import make_opik_client, resolve_opik_config
from opik_mcp.read_list import run_list, run_read
from opik_mcp.read_list.registry import LISTABLE_TYPES, READABLE_TYPES
from opik_mcp.run_experiment import run_experiment_impl
from opik_mcp.run_experiment_models import RunExperimentConfig, RunExperimentResult
from opik_mcp.writes import (
    SCHEMA_TOOL_DESCRIPTION,
    WRITE_TOOL_DESCRIPTION,
    run_schema,
    run_write,
)
from opik_mcp.writes.registry import WRITE_OPERATIONS

logger = logging.getLogger("opik_mcp")


# ---------------------------------------------------------------------------
# Per-tool analytics extras builders
# ---------------------------------------------------------------------------


def _looks_like_uuid(s: str) -> bool:
    from uuid import UUID

    try:
        UUID(s)
        return True
    except (ValueError, TypeError):
        return False


def _read_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    raw_id = kwargs.get("id", "")
    id_kind = (
        "uri"
        if str(raw_id).startswith("opik://")
        else ("uuid" if _looks_like_uuid(str(raw_id)) else "name")
    )
    return {
        "entity_type": kwargs.get("entity_type", ""),
        "id_kind": id_kind,
    }


def _list_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {
        "entity_type": kwargs.get("entity_type", ""),
        "had_name_filter": str(kwargs.get("name") is not None).lower(),
        "page": str(kwargs.get("page", 1)),
        "size": str(kwargs.get("size", 25)),
    }


def _write_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    """Analytics labels for the universal write tool.

    ``operation`` is the high-cardinality dimension that dashboards key off
    of; pair it with the boolean-ish shape signals so the (tool, operation)
    label set ADR §4.4 specifies stays useful as Phase 2 grows it.
    """
    data = kwargs.get("data")
    is_batch = isinstance(data, list)
    batch_size = len(data) if isinstance(data, list) else 1
    return {
        "operation": str(kwargs.get("operation", "")),
        "is_batch": str(is_batch).lower(),
        "batch_size_bucket": bucket_count(batch_size),
        "dry_run": str(bool(kwargs.get("dry_run", False))).lower(),
        "had_idempotency_key": str(kwargs.get("idempotency_key") is not None).lower(),
    }


def _schema_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {"operation": str(kwargs.get("operation", ""))}


def _ask_ollie_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {
        "had_continuation": str(kwargs.get("thread_id") is not None).lower(),
        "had_page_context": str(kwargs.get("page_context") is not None).lower(),
        "had_project_name": str(kwargs.get("project_name") is not None).lower(),
        "attach_resources_count": bucket_count(len(kwargs.get("attach_resources") or [])),
    }


def _run_experiment_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    cfg = kwargs.get("experiment_config") or {}
    prompts = cfg.get("prompts") if isinstance(cfg, dict) else None
    return {
        "prompt_count_bucket": bucket_count(len(prompts) if isinstance(prompts, list) else 0),
        "had_dataset_version_id": str(
            isinstance(cfg, dict) and bool(cfg.get("dataset_version_id"))
        ).lower(),
        "had_prompt_version": str(
            isinstance(prompts, list)
            and any(isinstance(p, dict) and bool(p.get("prompt_version_id")) for p in prompts)
        ).lower(),
    }


# ``instructions`` (ADR 0004 D6) is FastMCP's surface for the MCP
# InitializeResult.instructions field — hosts that support it inject the
# blob as system-prompt context once per session. Rendered eagerly so the
# server logs any settings issues at startup rather than mid-call.
mcp = FastMCP("opik-mcp", instructions=render_instructions())


# --- read / list (ADR 0004 D1) ------------------------------------------ #


@mcp.tool()
@instrument_tool("read", props_fn=_read_props)
async def read(
    entity_type: Annotated[
        str,
        Field(
            description=f"One of: {', '.join(sorted(READABLE_TYPES))}.",
            json_schema_extra={"enum": sorted(READABLE_TYPES)},
        ),
    ],
    id: Annotated[
        str,
        Field(
            description=(
                "UUID, entity name (for nameable types), or full opik:// URI "
                "(e.g. opik://traces/<uuid>). When a URI is passed, entity_type "
                "is overridden from the URI."
            ),
            min_length=1,
            max_length=512,
        ),
    ],
    max_tokens: Annotated[
        int | None,
        Field(
            description=(
                "Optional token budget. If the entity is under the budget, it's "
                "returned in full; otherwise compressed to MEDIUM (long strings "
                "truncated with path hints) or SKELETON (structure only). "
                "Default ~8k tokens."
            ),
            ge=100,
            le=200_000,
        ),
    ] = None,
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    """Read any Opik entity by ID, name, or opik:// URI, with adaptive compression.

    Prefer a UUID for `id` — it's faster (single API call) and unambiguous.
    Name lookup is available for: project, experiment, prompt, test_suite —
    name lookup is slower (two API calls) and may return multiple matches,
    in which case the tool lists the candidates so you can retry with the
    correct ID.

    Special shapes:
    - trace: returns {trace, spans, spansTruncated} with up to 200 spans inlined.
    - prompt: returns {prompt, versions, versionsTruncated} with up to 100 versions.
    - All others: the flat record from /v1/private/{entity}/{id}.

    Output is a one-line `[read: …]` header (entity_type, id, compression
    tier, returned tokens, full tokens) followed by compact JSON.
    """
    if ctx is not None:
        await ctx.info(f"read.called entity_type={entity_type} id={id}")
    return await run_read(entity_type=entity_type, id=id, max_tokens=max_tokens)


@mcp.tool(name="list")
@instrument_tool("list", props_fn=_list_props)
async def list_entities(
    entity_type: Annotated[
        str,
        Field(
            description=f"One of: {', '.join(sorted(LISTABLE_TYPES))}.",
            json_schema_extra={"enum": sorted(LISTABLE_TYPES)},
        ),
    ],
    name: Annotated[
        str | None,
        Field(
            description=(
                "Optional substring filter on entity name. Supported for project, "
                "experiment, prompt, test_suite; ignored for sub-collections."
            ),
            max_length=200,
        ),
    ] = None,
    page: Annotated[
        int,
        Field(description="Page number (1-indexed).", ge=1, le=10_000),
    ] = 1,
    size: Annotated[
        int,
        Field(description="Items per page. Capped at 100.", ge=1, le=100),
    ] = 25,
    project_id: Annotated[
        str | None,
        Field(description="Required when listing traces. UUID of the parent project."),
    ] = None,
    test_suite_id: Annotated[
        str | None,
        Field(description="Required when listing test_suite_items. UUID of the suite."),
    ] = None,
    prompt_id: Annotated[
        str | None,
        Field(description="Required when listing prompt_versions. UUID of the prompt."),
    ] = None,
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    """List Opik entities with optional name filter and pagination.

    Output is a pipe-delimited table with id, name, and a few entity-specific
    columns, plus a pagination footer when more pages exist. Use read() to
    get full details on any specific item.

    Project-scoped types require their parent id:
    - trace: project_id
    - test_suite_item: test_suite_id
    - prompt_version: prompt_id

    Workspace-wide types (project, experiment, prompt, test_suite) accept
    an optional `name` substring filter.
    """
    if ctx is not None:
        await ctx.info(f"list.called entity_type={entity_type} page={page} size={size}")
    return await run_list(
        entity_type=entity_type,
        name=name,
        page=page,
        size=size,
        project_id=project_id,
        test_suite_id=test_suite_id,
        prompt_id=prompt_id,
    )


# --- ask_ollie ----------------------------------------------------------- #


@mcp.tool()
@instrument_tool("ask_ollie", props_fn=_ask_ollie_props)
async def ask_ollie(
    query: Annotated[
        str,
        Field(
            description=(
                "The user's natural-language question for Ollie, the Opik in-product "
                "assistant. Ollie can read the caller's Opik workspace (traces, "
                "experiments, projects, prompts, datasets), summarize activity, and "
                "help debug LLM apps. Be specific — Ollie sees the workspace but not "
                "the surrounding chat. Example: 'Which traces from project demo failed "
                "today and why?'"
            ),
            max_length=10_000,
        ),
    ],
    page_context: Annotated[
        str | None,
        Field(
            description=(
                "Free-form markdown describing what the user is currently looking at "
                "in Opik (URL, selected trace, visible filters, etc.). This is a "
                "human-readable view snapshot — NOT the place to put structured "
                "project scope (use `project_name` for that). Ollie uses this for "
                "grounding when prose alone is ambiguous. Max ~30k chars."
            ),
            max_length=30_000,
        ),
    ] = None,
    attach_resources: Annotated[
        list[str] | None,
        Field(
            description=(
                "List of opik:// URIs (traces, spans, experiments, prompts, …) "
                "to materialize and hand to Ollie alongside the query. The MCP "
                "server pre-resolves each URI with the same parser the `read` "
                "tool uses. Currently a no-op pending the `ollie-assist` pod "
                "ChatRequest schema accepting the field — passing it is safe; "
                "it'll be wired through once the pod side lands."
            ),
        ),
    ] = None,
    thread_id: Annotated[
        str | None,
        Field(
            description=(
                "Thread id from a previous ask_ollie response. DEFAULT: reuse the "
                "most recent thread_id so Ollie keeps context across follow-ups. "
                "Omit ONLY on the first call, or when the user pivots to an "
                "unrelated topic (e.g. switches projects, asks about something "
                "new). When in doubt, reuse. IMPORTANT: Ollie does NOT persist "
                "project state across messages — if you set `project_name` on "
                "the first call, you must pass it again on every follow-up, "
                "even when continuing the same thread_id."
            ),
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        Field(
            description=(
                "Opik project name to scope Ollie's reads to. Without this, "
                "Ollie's read tools query the whole workspace. Pass on every "
                "call once you know which project the user is working in — "
                "Ollie does not persist project state across messages within "
                "a thread."
            ),
            max_length=200,
        ),
    ] = None,
    ctx: Context[ServerSession, None] | None = None,
) -> AskOllieResult:
    """Ask Ollie, the Opik in-product AI assistant, a question.

    Use this for investigative questions ("why did X fail?"), cross-entity
    synthesis, or when domain expertise is required. For straightforward
    "show me X" / "what is Y" reads, prefer `read` / `list` which are
    cheaper. Ollie has direct read access to the workspace; the surrounding
    chat does not.

    Returns the assistant's final text plus a `thread_id`. Reuse that
    `thread_id` on subsequent calls by default — Ollie has no memory across
    threads, so dropping it loses all prior context. Start a fresh thread
    (omit `thread_id`) only when the user explicitly changes topics.

    Writes Ollie performs mid-stream (scores, comments, test-suite items,
    prompts) execute without a per-action confirmation step; auto-approvals
    are recorded on the `opik_mcp.audit` logger.
    """
    return await run_ask_ollie(
        query=query,
        page_context=page_context,
        attach_resources=attach_resources,
        thread_id=thread_id,
        project_name=project_name,
        ctx=ctx,
    )


# --- run_experiment ----------------------------------------------------- #


_RUN_EXPERIMENT_DESCRIPTION = (
    "Submit an experiment on a test-suite-backed dataset. opik-backend runs "
    "each prompt variant against every item in the suite asynchronously and "
    "applies the suite's scoring assertions. Returns the created "
    "`experiment_ids` immediately — the run itself typically takes 10-30+ "
    "minutes server-side.\n\n"
    "This tool is fire-and-return: it does NOT wait for completion. To check "
    'progress, the caller uses `read("experiment", <id>)`; the experiment '
    "record carries `status` and `trace_count`. The result also includes a "
    "`summary_url` deep-linking the Opik UI compare view.\n\n"
    "Use for: rerunning an evaluation, trying a prompt on a known test suite, "
    "comparing models against the same test suite.\n\n"
    "Does NOT support ad-hoc (non-test-suite) datasets — those require "
    "client-side LLM execution which this tool intentionally does not do."
)


@mcp.tool(description=_RUN_EXPERIMENT_DESCRIPTION)
@instrument_tool("run_experiment", props_fn=_run_experiment_props)
async def run_experiment(
    experiment_config: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Experiment-execution config. Required: `dataset_name`, "
                "`dataset_id` (UUID of a test-suite dataset), `prompts` "
                "(non-empty list of `{model, messages, configs?, "
                "prompt_version_id?}`). Optional: `dataset_version_id`, "
                "`version_hash`, `project_name`. One experiment is created "
                'per prompt variant. Call `schema("run_experiment")` if '
                "the shape is unclear."
            )
        ),
    ],
    ctx: Context[ServerSession, None] | None = None,
) -> RunExperimentResult:
    """Run an experiment via opik-backend `/experiments/execute`."""
    config = RunExperimentConfig.model_validate(experiment_config)
    settings = get_settings()
    client = make_opik_client(settings)
    _, _, workspace = resolve_opik_config(settings)
    # Workspace may be None when an OAuth bearer arrives without a
    # Comet-Workspace header (the backend derives it from the token row, but
    # we need the name client-side to build the experiment summary URL).
    if workspace is None:
        raise MissingConfigError(
            "run_experiment requires a workspace — set COMET_WORKSPACE or "
            "send a Comet-Workspace header"
        )
    comet_base = settings.comet_url_override.rstrip("/")
    return await run_experiment_impl(
        config=config,
        client=client,
        comet_base_url=comet_base,
        workspace=workspace,
    )


# --- write / schema (universal write tool, supersedes score/comment) ---- #
#
# Operation is advertised as a JSON-Schema enum but typed as ``str`` so the
# FastMCP/Pydantic boundary does NOT reject unknown values — we want those
# to flow into the dispatcher's Stage 1 which raises ``UnknownOperationError``
# with the full ``valid_operations`` list. The conformance test verifies
# the advertised enum matches ``WRITE_OPERATIONS`` so drift is caught.

WRITE_OPERATION_ENUM: list[str] = list(WRITE_OPERATIONS)


@mcp.tool(description=WRITE_TOOL_DESCRIPTION)
@instrument_tool("write", props_fn=_write_props)
async def write(
    operation: Annotated[
        str,
        Field(
            description=(
                "The entity/verb pair to invoke. See tool description for the list. "
                "Call schema(operation) for the JSON Schema, example, and required scope."
            ),
            json_schema_extra={"enum": WRITE_OPERATION_ENUM},
        ),
    ],
    data: Annotated[
        dict[str, Any] | list[Any],
        Field(
            description=(
                "Payload for the operation. Object for a single write, or array "
                "(max 1000 elements) for batch. Always-envelope operations "
                "(test_suite_item.upsert, experiment_item.create) take their list "
                "inside the envelope, not at the top level."
            ),
        ),
    ],
    idempotency_key: Annotated[
        str | None,
        Field(
            description=(
                "Optional client-supplied UUID. Re-running with the same key is a "
                "no-op on the backend. Takes precedence over data.id when both are set."
            ),
            max_length=64,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "Validate against the operation's schema and OAuth scope without "
                "calling the backend. Returns {dry_run: true, would_call: ...}."
            ),
        ),
    ] = False,
    ctx: Context[ServerSession, None] | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        is_batch = isinstance(data, list)
        await ctx.info(f"write.called operation={operation} batch={is_batch} dry_run={dry_run}")
    return await run_write(
        operation=operation,
        data=data,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
    )


@mcp.tool(description=SCHEMA_TOOL_DESCRIPTION)
@instrument_tool("schema", props_fn=_schema_props)
async def schema(
    operation: Annotated[
        str,
        Field(
            description="Operation whose schema to return.",
            json_schema_extra={"enum": WRITE_OPERATION_ENUM},
        ),
    ],
    ctx: Context[ServerSession, None] | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        await ctx.info(f"schema.called operation={operation}")
    return run_schema(operation=operation)


# --- middleware ---------------------------------------------------------- #


# Probes are unauthenticated by design — Kubernetes liveness/readiness probes
# can't carry the bearer token and must remain reachable even when auth
# misconfiguration would otherwise return 401.
_HEALTH_PATHS = frozenset({"/health", "/health/ready"})

# Protected-resource metadata is the bootstrap entry point for the OAuth
# dance: MCP hosts fetch it (per RFC 9728) before they have any credentials,
# so it must be reachable without an Authorization header.
_PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"

# AS-discovery / OAuth-flow paths that MCP host SDKs probe on the resource
# server's host before they have a token. In production opik-mcp sits behind
# the same edge as opik-backend so these paths "just work" — but locally
# they're on different ports, so we redirect to the configured AS to keep
# the SDK's discovery chain unbroken. Anything not in this set or
# ``_HEALTH_PATHS`` requires a bearer.
_PROXIED_OAUTH_PATHS = {
    # AS metadata + OIDC fallback some SDKs probe before the protected-resource doc
    "/.well-known/oauth-authorization-server": "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration": "/.well-known/oauth-authorization-server",
    # OAuth 2.1 flow endpoints; SDK convention is to find them at the RS root
    "/register": "/oauth/register",
    "/authorize": "/oauth/authorize",
    "/token": "/oauth/token",
    "/revoke": "/oauth/revoke",
    "/oauth/register": "/oauth/register",
    "/oauth/authorize": "/oauth/authorize",
    "/oauth/token": "/oauth/token",
    "/oauth/revoke": "/oauth/revoke",
}

_UNAUTH_PATHS = (
    _HEALTH_PATHS
    | frozenset({_PROTECTED_RESOURCE_METADATA_PATH})
    | frozenset(_PROXIED_OAUTH_PATHS.keys())
)


def _is_unauth_path(path: str) -> bool:
    """Paths that bypass bearer auth: health, protected-resource metadata, the
    OAuth-flow proxy paths, and the path-prefixed ``.well-known`` variants some
    SDKs probe. Shared by ``BearerAuthMiddleware`` (skip the 401) and
    ``AuthRejectionMiddleware`` (don't attribute a proxied-AS 401 as our
    rejection) so the two can't drift.
    """
    return (
        path in _UNAUTH_PATHS
        or path.startswith("/.well-known/")
        or path.startswith("/mcp/.well-known/")
    )


# Bounded total budget for the upstream reachability check used by
# /health/ready. Probes run every 5-10s; a hung upstream must not stall the
# probe past the probe's own timeout, or readiness flips to "unknown" instead
# of "not ready" and traffic keeps flowing.
_READY_PROBE_TIMEOUT_S = 2.0


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Bearer-shape check + per-request bearer-capture for outbound forwarding.

    opik-mcp performs **no local credential validation**. Any well-formed
    ``Authorization: Bearer …`` header is accepted; the full header value is
    captured into a ContextVar and forwarded verbatim on the outbound call to
    opik-backend (see :mod:`opik_mcp.auth_context`). opik-backend's
    ``AuthFilter`` is the single point of auth enforcement — it validates the
    bearer (API key or an ``OAUTH_ACCESS_TOKEN_PREFIX``-prefixed OAuth token) and enforces
    ``@RequiredPermissions`` on the data API endpoint. Deployments where the
    backend enforces auth are protected end-to-end; OSS installs without
    backend auth are as open via MCP as via their own REST API.

    Missing/empty ``Authorization`` returns 401 with a ``WWW-Authenticate``
    header that points MCP hosts at
    ``/.well-known/oauth-protected-resource`` so they can bootstrap the
    OAuth dance per RFC 6750 + RFC 9728. Health probes and the metadata
    endpoint are exempt from auth.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        resource_metadata_url: str | None,
    ) -> None:
        super().__init__(app)
        self._resource_metadata_url = resource_metadata_url

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        # Discovery + bootstrap paths are unauthenticated by spec — RFC 9728
        # well-known metadata, OIDC/OAuth AS metadata, and the OAuth-flow
        # endpoints all run pre-credentials. Returning 401 on these would
        # break the host's discovery chain; many SDKs probe path-prefixed
        # variants too (``/.well-known/foo/mcp``, ``/mcp/.well-known/foo``),
        # so we accept the whole prefix.
        if _is_unauth_path(path):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth:
            return self._unauthorized()

        if not auth.lower().startswith("bearer ") or not auth[len("Bearer ") :].strip():
            # Require the canonical "Bearer <token>" shape — non-Bearer
            # schemes and empty tokens are rejected here rather than
            # forwarded, so the host gets the WWW-Authenticate hint and a
            # clean recovery path instead of an opaque upstream 401.
            return self._unauthorized()

        # Capture the inbound auth + workspace headers for the duration of
        # this request so the outbound :class:`OpikClient` can forward them.
        auth_token = inbound_authorization.set(auth)
        workspace = request.headers.get("comet-workspace")
        workspace_token = inbound_workspace.set(workspace)
        # On the session-creating request — the MCP ``initialize`` handshake is
        # the only one without an ``Mcp-Session-Id`` — resolve the OAuth-authorized
        # workspace NAME so the per-session instructions blob can name it. In OAuth
        # mode the host sends no ``Comet-Workspace`` header and the token is opaque
        # to us, so we introspect it here (once per session); tool calls carry a
        # session id and skip this. Best-effort: a failure leaves the blob on its
        # static fallback and never blocks the handshake.
        resolved_token = None
        if request.headers.get("mcp-session-id") is None and classify_bearer(auth)[0] == "oauth":
            workspace_name = await resolve_workspace_name(auth, get_settings())
            if workspace_name:
                resolved_token = resolved_workspace_name.set(workspace_name)
        try:
            return await call_next(request)
        finally:
            inbound_authorization.reset(auth_token)
            inbound_workspace.reset(workspace_token)
            if resolved_token is not None:
                resolved_workspace_name.reset(resolved_token)

    def _unauthorized(self) -> Response:
        # RFC 6750 §3 + RFC 9728: pointing MCP hosts at protected-resource
        # metadata is what kicks off automatic OAuth discovery — without
        # this, hosts have no way to find the AS without out-of-band config.
        headers: dict[str, str] = {}
        if self._resource_metadata_url:
            headers["WWW-Authenticate"] = (
                f'Bearer realm="opik-mcp", resource_metadata="{self._resource_metadata_url}"'
            )
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers=headers)


def _has_absolute_resource_metadata_url(settings: Settings) -> bool:
    """True only when ``OPIK_MCP_RESOURCE_URI`` is an absolute URL (scheme+netloc).

    ``_resource_metadata_url()`` returns a non-empty *relative* path even when the
    URI is unset, so ``bool()`` on it is always True. This answers the real
    question for BI: could a host actually bootstrap OAuth discovery from the
    ``WWW-Authenticate`` hint this rejection carried?
    """
    uri = settings.opik_mcp_resource_uri
    if not uri:
        return False
    parsed = urlparse(uri)
    return bool(parsed.scheme and parsed.netloc)


def _classify_rejection_reason(status_code: int, auth_header: str) -> str:
    """Map (status, Authorization-header shape) to a ``rejection_reason`` bucket.

    PRIVACY: inspects only the scheme keyword and whether a token is present —
    never stores or returns any part of the token value.
    """
    if status_code == 421:
        return "host_rejected"
    if status_code == 403:
        return "origin_rejected"
    # 401 — classify by header shape (mirrors BearerAuthMiddleware's checks).
    if not auth_header:
        return "missing_header"
    if not auth_header.lower().startswith("bearer "):
        return "not_bearer"
    if not auth_header[len("bearer ") :].strip():
        return "empty_token"
    # A well-formed bearer that still got a 401 — BearerAuthMiddleware forwards
    # those onward, so today this only arises if a downstream layer 401s a valid
    # shape. Its own bucket (never echoes the token) so BI doesn't conflate it
    # with genuinely-missing-header rejections.
    return "token_rejected"


class AuthRejectionMiddleware:
    """Outermost ASGI wrapper: emit ``opik_mcp_auth_rejected`` for 401/421/403
    responses on authenticated paths (GAP#3).

    Pure ASGI (NOT ``BaseHTTPMiddleware``) so streaming SSE responses flow
    through unbuffered — it reads only the response status line. ``_UNAUTH_PATHS``
    (health, OAuth-proxy, discovery) are skipped: a 401 proxied from the AS
    during the OAuth dance is not opik-mcp's resource-server rejection, and
    counting it would pollute the auth-rejection chart. Non-http scopes
    (``lifespan``/``websocket``) pass straight through.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self._settings = settings

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_holder: list[int] = []

        async def _capture(message: Any) -> None:
            # Record the FIRST response status only (ASGI spec sends exactly one
            # http.response.start; guard against a misbehaving app sending more).
            if message["type"] == "http.response.start" and not status_holder:
                status_holder.append(message["status"])
            await send(message)

        await self.app(scope, receive, _capture)

        if status_holder and status_holder[0] in (401, 403, 421):
            try:
                self._emit_rejection(scope, status_holder[0])
            except Exception:
                # Telemetry must never affect the (already-sent) response.
                logger.debug("auth_rejected emit failed", exc_info=True)

    def _emit_rejection(self, scope: dict[str, Any], status_code: int) -> None:
        path = scope.get("path", "")
        if _is_unauth_path(path):
            return
        auth_header = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                auth_header = value.decode("latin-1", "replace")
                break
        # auth_mode must be derived HERE from the header: this runs after
        # BearerAuthMiddleware reset the inbound-auth ContextVar, so
        # _build_event's per-request fallback would otherwise always be the
        # settings value (never the rejected bearer). Shape-only — no token kept.
        if auth_header:
            auth_mode, _token = classify_bearer(auth_header)
        else:
            # No credential: settings-derived mode (shared with auth_mode_at_boot
            # so an OAuth-only deploy reports "oauth", not "none").
            auth_mode = settings_auth_mode(
                has_api_key=bool(self._settings.opik_api_key),
                has_as_url=bool(self._settings.opik_mcp_as_url),
            )
        props = {
            "rejection_reason": _classify_rejection_reason(status_code, auth_header),
            "auth_mode": auth_mode,
            "path_bucket": bucket_path(path, self._settings.opik_mcp_http_path),
            "oauth_configured": boot_props.oauth_configured(self._settings),
            "resource_metadata_url_present": str(
                _has_absolute_resource_metadata_url(self._settings)
            ).lower(),
            # Env cohort so BI can separate CI/probe noise from real misconfigs.
            **cached_call_context_env(),
        }
        track_event(EVENT_AUTH_REJECTED, props)


# --- health endpoints ---------------------------------------------------- #


async def _liveness(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _readiness(_request: Request) -> JSONResponse:
    """Probe comet-backend reachability; fail closed.

    The MCP server is a thin relay to comet-backend / opik-backend, so
    "ready to serve" reduces to "upstream is reachable from this pod". A
    HEAD against the configured Comet base accepts any non-5xx as evidence
    the host is up — 4xx still means the TCP+TLS+HTTP stack works, which is
    what readiness actually cares about.

    The HTTP client is built per-request (no module-level singleton) so DNS
    changes — e.g., a comet-backend service IP rotation in Kubernetes —
    take effect on the next probe without a pod restart. Cost is negligible
    at probe frequency.
    """
    base = get_settings().comet_url_override.rstrip("/") or "https://www.comet.com"
    reason: str
    try:
        async with httpx.AsyncClient(timeout=_READY_PROBE_TIMEOUT_S) as client:
            resp = await client.head(base, follow_redirects=False)
    except httpx.TimeoutException:
        reason = "timeout"
    except httpx.NetworkError:
        # Genuine network failures only: ConnectError, ReadError, WriteError,
        # CloseError. Config bugs (InvalidURL, UnsupportedProtocol) are NOT
        # NetworkError and intentionally bubble to a 500 so a typo in
        # COMET_URL_OVERRIDE surfaces loudly instead of pinning the pod to
        # not_ready/network_error forever.
        reason = "network_error"
    else:
        if resp.status_code >= 500:
            reason = "upstream_5xx"
        else:
            return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not_ready", "reason": reason}, status_code=503)


async def _oauth_protected_resource(_request: Request) -> JSONResponse:
    """RFC 9728 protected-resource metadata.

    Returned to MCP hosts so they can discover the Authorization Server and
    run the OAuth dance against it without needing the AS URL preconfigured.

    When ``OPIK_MCP_AS_URL`` is unset, the discovery doc is unavailable —
    this opik-mcp instance is then only useful with ``OPIK_API_KEY``-style
    bearers. Returning 503 makes the misconfiguration loud and steers
    operators to set ``OPIK_MCP_AS_URL``.
    """
    s = get_settings()
    if not s.opik_mcp_as_url:
        return JSONResponse({"error": "OPIK_MCP_AS_URL not configured"}, status_code=503)
    body: dict[str, Any] = {
        "authorization_servers": [s.opik_mcp_as_url],
    }
    if s.opik_mcp_resource_uri:
        body["resource"] = s.opik_mcp_resource_uri
    return JSONResponse(body)


# Headers that must not be forwarded from inbound → outbound on the proxy
# path. ``host`` would override httpx's auto-set Host; ``content-length`` is
# recomputed from the body; hop-by-hop framing headers don't make sense to
# forward; and ``cookie`` is intentionally dropped because OAuth flows use
# the AS's own session cookies and we don't want to leak SDK cookies upstream.
_PROXY_DROP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "cookie"}
)

# Hop-by-hop response headers per RFC 7230 §6.1 — must not be forwarded back
# unchanged or httpx/Starlette's framing assumptions break.
_PROXY_DROP_RESPONSE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)


async def _proxy_to_as(request: Request) -> Response:
    """Proxy AS-flow / discovery requests to the configured AS host.

    MCP host SDKs probe ``/register``, ``/authorize``, ``/.well-known/oauth-
    authorization-server`` etc. at the resource server's host before they
    have a token. In production opik-mcp sits behind the same edge as
    opik-backend so these paths route correctly without ceremony. Locally
    (or in any split-host deploy) we proxy them to the configured AS so
    the SDK sees a same-origin response — earlier attempts using HTTP 307
    redirects failed because some SDKs do not follow cross-origin OAuth-
    discovery redirects.

    Proxying preserves method, body, query string, and most headers, and
    returns the AS response inline. The proxied AS metadata still contains
    absolute opik-backend URLs in its endpoint fields (``authorization_
    endpoint``, ``token_endpoint``, etc.) — SDKs that use those directly
    talk to the AS over the network; SDKs that probe ``/register`` etc. at
    the RS root land here again and we proxy that too.

    Returns 503 when ``OPIK_MCP_AS_URL`` is unset — the only safe answer:
    we don't know where to send the probe, and silently 404'ing would
    mislead clients into thinking the resource doesn't support OAuth.
    """
    s = get_settings()
    if not s.opik_mcp_as_url:
        return JSONResponse({"error": "OPIK_MCP_AS_URL not configured"}, status_code=503)
    target_path = _PROXIED_OAUTH_PATHS.get(request.url.path)
    if target_path is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    qs = request.url.query
    target = f"{s.opik_mcp_as_url.rstrip('/')}{target_path}"
    if qs:
        target = f"{target}?{qs}"
    body = await request.body()
    forwarded_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _PROXY_DROP_REQUEST_HEADERS
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        upstream = await client.request(
            method=request.method,
            url=target,
            headers=forwarded_headers,
            content=body,
        )
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _PROXY_DROP_RESPONSE_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


def _resource_metadata_url(settings: Settings) -> str | None:
    """Build the absolute URL we advertise in ``WWW-Authenticate``.

    The metadata route is registered at the application root, so the URL we
    advertise must match: derived from the resource URI's scheme + authority
    rather than appended under its path. RFC 9728 §3.1 permits both
    path-prefixed and host-relative forms; MCP hosts in practice follow the
    host-relative form, and our Starlette ``Route`` registration sits at
    ``/`` + the well-known path (not nested under ``/mcp``). Appending under
    the resource path produces a URL that 404s (or falls through to the MCP
    path's auth middleware and 401s), silently breaking host bootstrap.

    Falls back to the bare relative path when no public URI is configured —
    still useful for hosts that resolve relative to the 401 URL, though that
    pathway has the same authority as the request that triggered it.
    """
    if settings.opik_mcp_resource_uri:
        parsed = urlparse(settings.opik_mcp_resource_uri)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{_PROTECTED_RESOURCE_METADATA_PATH}"
    return _PROTECTED_RESOURCE_METADATA_PATH


async def _not_found_json(scope: Any, receive: Any, send: Any) -> None:
    """Default-route handler — returns 404 as JSON instead of ``text/plain``.

    Starlette's stock 404 is ``Content-Type: text/plain`` with body ``Not
    Found``. MCP host SDKs that JSON-parse every response (including
    discovery probes that legitimately end in 404) choke on the plain
    text and abort their bootstrap with "Failed to parse JSON". Returning
    a tiny JSON envelope keeps every error response on the canonical
    content-type contract.
    """
    response = JSONResponse({"error": "not_found"}, status_code=404)
    await response(scope, receive, send)


# Shutdown drain budget for the lifespan path. Mirrors __main__'s deadline: the
# daemon worker is about to be torn down, so block briefly to land the POST.
_LIFESPAN_FLUSH_DEADLINE_S = 3.5


def _make_composed_lifespan(
    inner_lifespan: Any,
    settings: Settings,
    fingerprint_props: dict[str, str],
) -> Any:
    """Wrap FastMCP's session-manager lifespan with analytics lifecycle emits.

    Closes GAP#1: the hosted entrypoint runs ``uvicorn ... build_app --factory``,
    which calls ``build_app()`` directly and never runs ``__main__.main()``, so
    the boot funnel was 100% dark. This emits server_started/shutdown from the
    lifespan instead — UNLESS ``main()`` owns lifecycle (the sentinel), in which
    case it just runs the inner lifespan and emits nothing (no double-count).

    ``inner_lifespan`` MUST be captured from ``app.router.lifespan_context``
    BEFORE it is overwritten — it starts the ``StreamableHTTPSessionManager``,
    without which every MCP request hangs.
    """

    @contextlib.asynccontextmanager
    async def _composed(app: Any) -> AsyncIterator[Any]:
        if boot_props.lifecycle_owned_by_main():
            # main() emits the lifecycle events; just run the session manager.
            async with inner_lifespan(app) as state:
                yield state
            return

        # Anchor at lifespan enter (when serving actually starts), not at
        # build_app() time — keeps lifespan_seconds_bucket free of uvicorn's
        # startup/bind latency.
        started_monotonic = time.monotonic()
        try:
            track_event(
                EVENT_SERVER_STARTED,
                boot_props.server_started_props(
                    settings, fingerprint_props=fingerprint_props, lifecycle_source="lifespan"
                ),
            )
        except Exception:
            logger.debug("lifespan server_started emit failed", exc_info=True)

        reason = "clean_exit"
        try:
            async with inner_lifespan(app) as state:
                yield state
        except BaseException:
            reason = "transport_error"
            raise
        finally:
            try:
                elapsed = time.monotonic() - started_monotonic
                track_event(
                    EVENT_SERVER_SHUTDOWN,
                    boot_props.server_shutdown_props(
                        reason=reason, elapsed_seconds=elapsed, lifecycle_source="lifespan"
                    ),
                )
                # flush() blocks on threading.Event.wait — never block the event
                # loop; offload the drain to the default executor. Capture the
                # client now (not inside the thread) so a concurrent singleton
                # swap can't redirect the flush to a different/closed client.
                client = get_analytics()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, lambda: client.flush(deadline_s=_LIFESPAN_FLUSH_DEADLINE_S)
                )
            except BaseException:
                # Mirror __main__._emit_server_shutdown: a telemetry-side failure
                # (incl. CancelledError from the executor during loop teardown)
                # must NEVER mask the real shutdown reason or leak out of finally.
                logger.debug("lifespan server_shutdown emit failed", exc_info=True)

    return _composed


def install_session_instructions(server: FastMCP) -> None:
    """Render ``InitializeResult.instructions`` per session rather than once at boot.

    FastMCP captures the ``instructions`` string at construction time, so the blob
    would be identical for every session and could never name the per-session
    OAuth workspace. We wrap the lowlevel server's ``create_initialization_options``
    (invoked once per session, inside the session task that inherits the
    ``initialize`` request's ContextVars) to re-render the blob with the workspace
    resolved for that session. Mirrors ``install_tools_listed_emitter``'s in-place
    handler swap. The boot-time static render stays on the options as a fallback.
    """
    try:
        lowlevel = server._mcp_server
    except AttributeError:
        logger.debug("install_session_instructions: mcp has no _mcp_server attribute")
        return
    original = lowlevel.create_initialization_options

    def create_initialization_options(*args: Any, **kwargs: Any) -> Any:
        options = original(*args, **kwargs)
        try:
            options.instructions = render_instructions()
        except Exception:
            # A render hiccup must never break the initialize handshake — leave
            # the boot-time static instructions already on the options in place.
            logger.debug("per-session instructions render failed", exc_info=True)
        return options

    lowlevel.create_initialization_options = create_initialization_options  # type: ignore[method-assign]


def build_app() -> ASGIApp:
    install_tools_listed_emitter(mcp)
    install_session_instructions(mcp)
    s = get_settings()
    # Serve the transport at the configured path so it matches the advertised
    # resource URI behind a non-rewriting path-prefix proxy. Read at app-build
    # time (streamable_http_app reads it then), so env overrides take effect.
    mcp.settings.streamable_http_path = s.opik_mcp_http_path
    # DNS-rebinding/Host-Origin guard. Default allow-lists cover localhost only,
    # so hosted deployments must add their public host (and browser-client
    # origins). Applied here for the same read-at-build-time reason as above.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=s.opik_mcp_dns_rebinding_protection,
        allowed_hosts=s.allowed_hosts_list,
        allowed_origins=s.allowed_origins_list,
    )
    app = mcp.streamable_http_app()
    # Replace Starlette's default plain-text 404 — see ``_not_found_json``.
    app.router.default = _not_found_json
    app.router.routes.append(Route("/health", _liveness, methods=["GET"]))
    app.router.routes.append(Route("/health/ready", _readiness, methods=["GET"]))
    app.router.routes.append(
        Route(
            _PROTECTED_RESOURCE_METADATA_PATH,
            _oauth_protected_resource,
            methods=["GET"],
        )
    )
    # AS / OAuth-flow probe paths — proxy to the configured AS so
    # split-host deployments (local docker-compose, dev clusters where
    # opik-mcp and opik-backend bind to different addresses) work the
    # same as the production single-edge deploy. Proxying (not redirect)
    # because some SDKs refuse to follow cross-origin OAuth-discovery
    # redirects and silently break their bootstrap.
    for path in _PROXIED_OAUTH_PATHS:
        app.router.routes.append(Route(path, _proxy_to_as, methods=["GET", "POST"]))
    s = get_settings()
    app.add_middleware(
        BearerAuthMiddleware,
        resource_metadata_url=_resource_metadata_url(s),
    )

    # GAP#1: emit lifecycle events from the lifespan so the hosted --factory
    # entrypoint (which bypasses __main__.main()) is no longer dark. Capture the
    # session-manager lifespan BEFORE overwriting it. Compute the fingerprint
    # synchronously here (it shells out on macOS — must not run in the async
    # lifespan) and only when this process will actually emit: a main()-owned
    # boot skips the emit, so skip the cost too.
    will_emit = not boot_props.lifecycle_owned_by_main()
    fingerprint_props = collect_environment_fingerprint() if will_emit else {}
    inner_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _make_composed_lifespan(inner_lifespan, s, fingerprint_props)

    # Outermost wrapper: observe 401/421/403 and emit auth_rejected (GAP#3). Pure
    # ASGI so streaming SSE is never buffered; the "lifespan" scope passes through
    # to the Starlette app so the composed lifespan above still runs.
    return AuthRejectionMiddleware(app, settings=s)

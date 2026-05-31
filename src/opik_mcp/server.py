import logging
import secrets
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from opik_mcp.analytics.events import bucket_count
from opik_mcp.analytics.wrappers import install_tools_listed_emitter, instrument_tool
from opik_mcp.ask_ollie import AskOllieResult, run_ask_ollie
from opik_mcp.auth_context import inbound_authorization, inbound_workspace
from opik_mcp.config import Settings, get_settings
from opik_mcp.instructions import render_instructions
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
_UNAUTH_PATHS = _HEALTH_PATHS | frozenset({_PROTECTED_RESOURCE_METADATA_PATH})

# Bounded total budget for the upstream reachability check used by
# /health/ready. Probes run every 5-10s; a hung upstream must not stall the
# probe past the probe's own timeout, or readiness flips to "unknown" instead
# of "not ready" and traffic keeps flowing.
_READY_PROBE_TIMEOUT_S = 2.0


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Inbound auth + per-request bearer-capture for outbound forwarding.

    Two modes selected at construction time from ``OPIK_MCP_DEV_TOKEN_ENABLED``:

    * **OAuth-passthrough (default).** Any well-formed
      ``Authorization: Bearer …`` header is accepted. The full header value
      is captured into a ContextVar and forwarded verbatim on the outbound
      call to opik-backend (see :mod:`opik_mcp.auth_context`). opik-backend's
      ``AuthFilter`` validates the bearer (API key or ``opik_at_…`` OAuth
      token) and enforces ``@RequiredPermissions`` on the data API endpoint;
      opik-mcp performs no local validation.

    * **Dev-token mode** (``OPIK_MCP_DEV_TOKEN_ENABLED=true``). Strict
      ``constant_time`` comparison against ``OPIK_MCP_DEV_TOKEN``. Local
      testing scaffolding; ``__main__`` refuses to start when this mode is
      enabled with the default token on a non-loopback bind.

    In both modes, missing/empty ``Authorization`` returns 401 with a
    ``WWW-Authenticate`` header that points MCP hosts at
    ``/.well-known/oauth-protected-resource`` so they can bootstrap the
    OAuth dance per RFC 6750 + RFC 9728. Health probes and the metadata
    endpoint are exempt from auth.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        dev_token_enabled: bool,
        dev_token: str,
        resource_metadata_url: str | None,
    ) -> None:
        super().__init__(app)
        self._dev_token_enabled = dev_token_enabled
        self._expected_dev_token = f"Bearer {dev_token}"
        self._resource_metadata_url = resource_metadata_url

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _UNAUTH_PATHS:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth:
            return self._unauthorized()

        if self._dev_token_enabled:
            if not secrets.compare_digest(auth, self._expected_dev_token):
                return self._unauthorized()
        elif not auth.lower().startswith("bearer "):
            # OAuth-passthrough still requires the canonical "Bearer …" shape
            # so the WWW-Authenticate hint we return on failure is consistent
            # with what the host expects to send next.
            return self._unauthorized()

        # Capture the inbound auth + workspace headers for the duration of
        # this request so the outbound :class:`OpikClient` can forward them.
        auth_token = inbound_authorization.set(auth)
        workspace = request.headers.get("comet-workspace")
        workspace_token = inbound_workspace.set(workspace)
        try:
            return await call_next(request)
        finally:
            inbound_authorization.reset(auth_token)
            inbound_workspace.reset(workspace_token)

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
    auth (or dev-token mode). Returning 503 makes the misconfiguration loud
    and steers operators to set ``OPIK_MCP_AS_URL``.
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


def _resource_metadata_url(settings: Settings) -> str | None:
    """Build the absolute URL we advertise in ``WWW-Authenticate``.

    Prefers ``OPIK_MCP_RESOURCE_URI + /.well-known/oauth-protected-resource``;
    falls back to a relative path when no public URI is configured, which is
    still useful for hosts that resolve relative to the 401 URL.
    """
    if settings.opik_mcp_resource_uri:
        return f"{settings.opik_mcp_resource_uri.rstrip('/')}{_PROTECTED_RESOURCE_METADATA_PATH}"
    return _PROTECTED_RESOURCE_METADATA_PATH


def build_app() -> Starlette:
    install_tools_listed_emitter(mcp)
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health", _liveness, methods=["GET"]))
    app.router.routes.append(Route("/health/ready", _readiness, methods=["GET"]))
    app.router.routes.append(
        Route(
            _PROTECTED_RESOURCE_METADATA_PATH,
            _oauth_protected_resource,
            methods=["GET"],
        )
    )
    s = get_settings()
    app.add_middleware(
        BearerAuthMiddleware,
        dev_token_enabled=s.opik_mcp_dev_token_enabled,
        dev_token=s.opik_mcp_dev_token,
        resource_metadata_url=_resource_metadata_url(s),
    )
    return app

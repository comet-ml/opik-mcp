import logging
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from opik_mcp.analytics.events import bucket_count, bucket_text_len
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.ask_ollie import AskOllieResult, run_ask_ollie
from opik_mcp.config import get_settings
from opik_mcp.instructions import render_instructions
from opik_mcp.read_list import run_list, run_read
from opik_mcp.read_list.registry import LISTABLE_TYPES, READABLE_TYPES
from opik_mcp.score_comment import (
    CommentResult,
    ScoreResult,
    Target,
    run_comment,
    run_score,
)

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


def _hello_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {"name_was_default": str(kwargs.get("name", "world") == "world").lower()}


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


def _score_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    target = kwargs.get("target")
    target_type = getattr(target, "type", "") if target is not None else ""
    name = kwargs.get("name", "")
    canonical = {"helpfulness", "hallucination", "tone"}
    return {
        "target_type": str(target_type),
        "score_name_bucket": name if name in canonical else "other",
        "has_reason": str(kwargs.get("reason") is not None).lower(),
        "has_category": str(kwargs.get("category_name") is not None).lower(),
    }


def _comment_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    target = kwargs.get("target")
    return {
        "target_type": str(getattr(target, "type", "") if target is not None else ""),
        "text_length_bucket": bucket_text_len(kwargs.get("text", "")),
    }


def _ask_ollie_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {
        "had_continuation": str(kwargs.get("thread_id") is not None).lower(),
        "had_page_context": str(kwargs.get("page_context") is not None).lower(),
        "had_project_name": str(kwargs.get("project_name") is not None).lower(),
        "attach_resources_count": bucket_count(len(kwargs.get("attach_resources") or [])),
    }


# ``instructions`` (ADR 0004 D6) is FastMCP's surface for the MCP
# InitializeResult.instructions field — hosts that support it inject the
# blob as system-prompt context once per session. Rendered eagerly so the
# server logs any settings issues at startup rather than mid-call.
mcp = FastMCP("opik-mcp", instructions=render_instructions())


@mcp.tool()
@instrument_tool("hello", props_fn=_hello_props)
async def hello(
    name: Annotated[str, Field(description="Who to greet. Defaults to 'world'.")] = "world",
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    """Say hello. Returns a friendly greeting. Useful as a connectivity smoke test."""
    if ctx is not None:
        await ctx.info(f"hello.called name={name}")
    return f"hello, {name}"


# --- read / list (ADR 0004 D1) ------------------------------------------ #


@mcp.tool()
@instrument_tool("read", props_fn=_read_props)
async def read(
    entity_type: Annotated[
        str,
        Field(description=f"One of: {', '.join(sorted(READABLE_TYPES))}."),
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
        Field(description=f"One of: {', '.join(sorted(LISTABLE_TYPES))}."),
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


# --- score / comment ----------------------------------------------------- #


@mcp.tool()
@instrument_tool("score", props_fn=_score_props)
async def score(
    target: Annotated[
        Target,
        Field(
            description=(
                "What you're annotating. Object with two fields: "
                "`type` (one of 'trace', 'span', 'thread') and `id` (the entity's UUID). "
                "For threads, `id` is the thread's UUID (visible on the thread page in "
                "Opik), not a free-form thread label."
            ),
        ),
    ],
    name: Annotated[
        str,
        Field(
            description=(
                "Score name — short, snake_case-ish identifier. Examples: 'helpfulness', "
                "'hallucination', 'tone'. Multiple scores with different names can coexist "
                "on the same entity; scoring with the same `name` again overwrites the "
                "previous value."
            ),
            min_length=1,
            max_length=200,
        ),
    ],
    value: Annotated[
        float,
        Field(
            description=(
                "Numeric score value. Convention is 0.0-1.0 for graded metrics, 0/1 for "
                "boolean pass/fail, but any real number is accepted by the backend."
            ),
            ge=-1_000_000_000.0,
            le=1_000_000_000.0,
        ),
    ],
    reason: Annotated[
        str | None,
        Field(
            description=(
                "Optional free-text justification (≤2000 chars). Shown next to the score "
                "in the Opik UI."
            ),
            max_length=2000,
        ),
    ] = None,
    category_name: Annotated[
        str | None,
        Field(
            description=(
                "Optional bucket label for grouping scores (e.g. 'manual', 'auto', "
                "'llm-as-judge'). Free-form; the backend does not enforce values."
            ),
            max_length=200,
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        Field(
            description=(
                "Only consulted when `target.type` is 'thread'. Disambiguates the thread "
                "by project if your workspace has the same thread id in multiple projects. "
                "Ignored for trace/span targets — those are entity-implicit."
            ),
            max_length=200,
        ),
    ] = None,
    ctx: Context[ServerSession, None] | None = None,
) -> ScoreResult:
    """Attach a numeric feedback score to a trace, span, or thread.

    Use this for human-in-the-loop or programmatic evaluation labels — anything
    you'd want to filter or chart later. For investigative "why did X fail?"
    questions, use `ask_ollie` instead. To attach prose without a number, use
    `comment`.
    """
    if ctx is not None:
        await ctx.info(f"score.called target={target.type}:{target.id} name={name}")
    return await run_score(
        target=target,
        name=name,
        value=value,
        reason=reason,
        category_name=category_name,
        project_name=project_name,
    )


@mcp.tool()
@instrument_tool("comment", props_fn=_comment_props)
async def comment(
    target: Annotated[
        Target,
        Field(
            description=(
                "What you're commenting on. Object with `type` ('trace', 'span', or "
                "'thread') and `id` (the entity's UUID)."
            ),
        ),
    ],
    text: Annotated[
        str,
        Field(
            description=(
                "Comment body. Plain text or markdown — the Opik UI renders it. Required "
                "and non-empty (the backend rejects blank text)."
            ),
            min_length=1,
            max_length=10_000,
        ),
    ],
    ctx: Context[ServerSession, None] | None = None,
) -> CommentResult:
    """Attach a free-text comment to a trace, span, or thread.

    Use for prose annotations the user wants captured alongside an entity
    ("retry this with temperature=0", "regression vs. yesterday's run"). For
    numeric labels use `score`; for investigative questions use `ask_ollie`.
    """
    if ctx is not None:
        await ctx.info(f"comment.called target={target.type}:{target.id}")
    return await run_comment(target=target, text=text)


# --- middleware ---------------------------------------------------------- #


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.headers.get("authorization", "") != f"Bearer {self._token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app() -> Starlette:
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=get_settings().opik_mcp_dev_token)
    return app

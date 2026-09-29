from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field

from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.cost_intelligence import DEFAULT_MODE, Mode
from opik_mcp.cost_intelligence.descriptions import READ_DESCRIPTION
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.registry import READABLE_TYPES, URI_PATTERNS
from opik_mcp.read_list.uri import looks_like_opik_link
from opik_mcp.server.tools.fields import FIELDS_READ_DESCRIPTION
from opik_mcp.server.tools.hints import READS


def _looks_like_uuid(s: str) -> bool:
    try:
        UUID(s)
        return True
    except (ValueError, TypeError):
        return False


def _read_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    raw_id = str(kwargs.get("id", ""))
    if raw_id.startswith("opik://") or looks_like_opik_link(raw_id, URI_PATTERNS):
        id_kind = "uri"
    elif _looks_like_uuid(raw_id):
        id_kind = "uuid"
    else:
        id_kind = "name"
    return {
        "entity_type": kwargs.get("entity_type", ""),
        "id_kind": id_kind,
        # How many fields, never which: a path is ``data.<key>`` or
        # ``metadata.<key>``, which is the user's vocabulary and not ours to
        # put on an event. The count answers the question the feature will be
        # judged on — whether agents ask for one field or for most of them.
        "field_count": str(len(kwargs.get("fields") or [])),
    }


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
                "UUID, entity name (for nameable types), full opik:// URI "
                "(e.g. opik://traces/<uuid>), or a pasted Opik link — a thread "
                "link or a Diagnostics page link (…/projects/<pid>/diagnostics"
                "?issue=<id>). When a URI/link is passed, entity_type (and, for "
                "project-scoped entities, the project) is overridden from it."
            ),
            min_length=1,
            max_length=2048,
        ),
    ],
    project_id: Annotated[
        str | None,
        Field(
            description=(
                "Project UUID. Required for entity_type='thread' and "
                "'agent_insights_issue' unless the id is a full Opik URL/URI (which "
                "carries the project). Ignored for globally-unique entities like "
                "trace/span."
            ),
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        Field(
            description=(
                "Project name — alternative to project_id for project-scoped reads "
                "(thread, agent_insights_issue)."
            ),
            max_length=200,
        ),
    ] = None,
    since: Annotated[
        str | None,
        Field(
            description=(
                "Start of the window, as a relative span ('7d', '24h') or an ISO-8601 "
                "instant with timezone. On read: project (the summary's period, 7d by "
                "default) and agent_insights_issue (per-day details, truncated to UTC "
                "report days, all-time by default). Rejected for other types."
            ),
            max_length=40,
        ),
    ] = None,
    until: Annotated[
        str | None,
        Field(description="End of the window, same forms as since.", max_length=40),
    ] = None,
    fields: Annotated[
        list[str] | None,
        Field(description=FIELDS_READ_DESCRIPTION, max_length=50),
    ] = None,
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    """Read any Opik entity by ID, name, or opik:// URI.

    A UUID `id` costs one API call. A name also works for project,
    experiment, prompt and dataset: two calls, and a name several records
    share returns them as candidates to retry by id.

    Special shapes:
    - project: returns {project, summary, vocabulary, contains, url} — the
      week's figures against the 7 days before (SDK traffic only, as the Logs
      cards show, though the UI opens on 30 days), the score names and usage
      keys to filter on, and the freshest experiment / dataset / prompt version
      / run. `since`/`until` move the summary's window; the rest is current.
    - trace: returns {trace, spans, spansTruncated} with up to 200 spans
      inlined, their bodies slim, and spanBodies saying what the cut took.
    - prompt: returns {prompt, versions, versionsTruncated} with up to 100 versions.
    - thread: returns {thread, messages, messagesTruncated, messageBodies} —
      each message is one turn's trace input/output + a trace_id to
      read('trace', id). Bodies are slim, the thread's own first/last message
      included, since those are copies of the first and last turn. Needs
      project scope: pass a thread link/URI, or project_id/project_name.
    - An inlined collection longer than what fits sets its …Truncated flag and
      adds moreSpans / moreMessages / moreVersions: the count, and the exact
      list(...) call that continues from where the inlined part stopped.
    - agent_insights_issue: returns {issue, example_trace_ids, details, url,
      trace_url_template} — the Diagnostics issue with cause and suggested fix,
      the deduped ids of traces that exhibit it (open one with read('trace', id)),
      the per-day breakdown, the issue's Diagnostics page link, and a template
      for linking any example trace (both links omitted when the Opik URL or
      the session's workspace is unknown). Needs project scope like thread.
    - All others: the flat record from /v1/private/{entity}/{id}.

    Output is a one-line `[read: …]` header (entity_type, id, size in
    tokens) followed by the record as compact JSON. The record you asked for
    is never truncated: a large answer is large, and narrowing is done by
    asking a narrower question — `fields` to name the paths you want back, a
    span rather than its trace, a filtered `list` rather than a composite
    read. A `fields` answer says in its header that it was projected.

    The children a composite read inlines are the exception. Their bodies come
    back slim: a field over ~10 KB is cut and base64 images are replaced with
    "[image]", which is what keeps one attachment from costing more than the
    other 199 spans together. The read says so in `spanBodies` /
    `messageBodies`, and the named child is whole again through its own
    read('span', id) or read('trace', trace_id).
    """
    if ctx is not None:
        await ctx.info(f"read.called entity_type={entity_type} id={id}")
    return await run_read(
        entity_type=entity_type,
        id=id,
        project_id=project_id,
        project_name=project_name,
        since=since,
        until=until,
        fields=fields,
    )


def register(mcp: FastMCP[object], mode: Mode = DEFAULT_MODE) -> None:
    mcp.tool(
        description=READ_DESCRIPTION if mode != DEFAULT_MODE else None,
        title="Read an Opik record",
        annotations=READS,
        structured_output=False,
    )(read)

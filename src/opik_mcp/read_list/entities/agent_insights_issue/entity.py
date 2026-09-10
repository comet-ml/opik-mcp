"""``agent_insights_issue`` — a Diagnostics issue, read and listed.

The recurring failures Opik's Diagnostics job grouped for a project. The
entity's own oddity is that an empty list means five different things, so
``state`` writes the sentence that says which, and ``availability`` answers
whether the deployment has Diagnostics at all.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.entities.agent_insights_issue.state import issue_page_note
from opik_mcp.read_list.handler import EntityHandler, ReadWindow
from opik_mcp.read_list.project_scope import require_project_id
from opik_mcp.read_list.ui_links import project_page_url


def example_trace_ids(details: list[dict[str, Any]]) -> list[str]:
    """Deduplicated union of each per-day row's ``metadata.example_trace_ids``.

    First-seen order over the rows as the backend returns them (ascending
    report day), which is what the Diagnostics page's affected-traces sample
    shows. ``metadata`` is free-form JSON written by the Diagnostics job — a
    row without it, or with a non-object value, contributes nothing rather
    than failing the read.
    """
    seen: dict[str, None] = {}
    for row in details:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        ids = metadata.get("example_trace_ids")
        if not isinstance(ids, list):
            continue
        for trace_id in ids:
            if isinstance(trace_id, str) and trace_id:
                seen.setdefault(trace_id, None)
    return list(seen)


async def fetch(
    client: OpikReadClient,
    entity_id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Diagnostics issue + deduped example trace ids + per-day breakdown.

    One backend call. Returns ``{issue, example_trace_ids, details}`` plus a
    private ``_project_id`` (see below): the issue record without its
    ``details`` array, the trace ids the agent can open with
    ``read('trace', id)``, and the per-day rows unchanged. Trace bodies are
    deliberately not inlined — that would turn one read into N+1 calls on any
    issue with several examples. Nothing here is slimmed either: the
    agent-insights endpoints take no ``truncate`` parameter, and inventing a
    Python-side cut is the thing :mod:`opik_mcp.read_list.size` exists to say
    we do not do.
    """
    # The agent-insights endpoints take project_id only; resolve the name here
    # so the read contract stays "project_id or project_name" for every
    # project-scoped entity. An explicit project_id always wins.
    project_id = await require_project_id(
        client,
        project_id=project_id,
        project_name=project_name,
        caller="read('agent_insights_issue')",
    )
    body = await client.get_agent_insights_issue(
        entity_id, project_id=project_id, from_date=from_date, to_date=to_date
    )
    details_raw = body.get("details")
    details = (
        [row for row in details_raw if isinstance(row, dict)]
        if isinstance(details_raw, list)
        else []
    )
    issue = {key: value for key, value in body.items() if key != "details"}
    return {
        "issue": issue,
        # The link_fn needs the project the issue was read under; the backend
        # record does not carry it. Underscore-prefixed keys are stripped by
        # the read tool after links are attached, so it never reaches the agent.
        "_project_id": project_id,
        "example_trace_ids": example_trace_ids(details),
        "details": details,
    }


def issue_links(settings: Settings, data: dict[str, Any]) -> dict[str, str]:
    """The issue's Diagnostics page (open or resolved view, by status) and a
    template for deep-linking any of its example traces — the two links the
    diagnose skill has to hand the user."""
    project_id = data.get("_project_id")
    issue = data.get("issue") or {}
    issue_id = issue.get("id")
    if not isinstance(project_id, str) or not isinstance(issue_id, str):
        return {}
    view = "diagnostics" if issue.get("status") == "open" else "diagnostics/resolved"
    page = project_page_url(settings, project_id, f"{view}?issue={issue_id}")
    traces = project_page_url(settings, project_id, "logs?trace={trace_id}")
    if page is None or traces is None:
        return {}
    return {"url": page, "trace_url_template": traces}


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # "What is broken" means open issues, so that is the default; the caller
    # asks for resolved/closed explicitly. No name filter exists on the backend.
    # No ``sorting`` is sent: the backend's default (last seen, then total
    # occurrences) is the Diagnostics page's ranking.
    kw.pop("name", None)
    kw.setdefault("status", "open")
    # The backend takes project_id only. The list tool lets project_name
    # satisfy the project requirement (as for trace/thread), so resolve it
    # here; an explicit project_id wins and skips the lookup. Not `scope_of`
    # like its two neighbours: the name has to leave ``kw`` as well, since
    # what remains is forwarded to a client method that has no such parameter.
    kw["project_id"] = await require_project_id(
        client,
        project_id=kw.get("project_id"),
        project_name=kw.pop("project_name", None),
        caller="list('agent_insights_issue')",
    )
    return await client.list_agent_insights_issues(**kw)


HANDLER = EntityHandler(
    entity_type="agent_insights_issue",
    fetch_fn=fetch,
    list_fn=list_page,
    list_extra_fields=(
        "severity",
        "status",
        "total_occurrences",
        "latest_count",
        "last_seen",
    ),
    list_required_kwargs=("project_id",),
    list_optional_kwargs=("status", "from_date", "to_date"),
    page_note_fn=issue_page_note,
    # The Diagnostics endpoints key on whole UTC report days, so the
    # window is cut to dates rather than forwarded as instants. This
    # supersedes the `read_optional_kwargs=("from_date", "to_date")` that
    # declared the same window before the shape became per-entity.
    read_window=ReadWindow("from_date", "to_date", day_truncated=True),
    link_fn=issue_links,
    id_only=True,
    needs_project=True,
    description=(
        "Diagnostics issue (Agent Insights): a recurring failure the "
        "Diagnostics job grouped across a project's traces, with severity, "
        "status, occurrence counts, cause and suggested fix. "
        "list('agent_insights_issue', project_id=… | project_name=…) returns "
        "open issues ranked as the Diagnostics page ranks them (most recently "
        "seen first); pass status='resolved' or 'closed' for the rest. "
        "read('agent_insights_issue', id, project_id=…) returns {issue, "
        "example_trace_ids, details, url, trace_url_template}: the record with "
        "cause and suggested fix, the deduped ids of traces that exhibit it "
        "(open one with read('trace', id)), the per-day breakdown, and UI links "
        "to hand the user (omitted when the Opik URL or workspace is unknown). "
        "Counts are all-time unless since/until narrow the window (truncated "
        "to UTC report days)."
    ),
)

__all__ = ["HANDLER"]

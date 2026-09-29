"""``spend_session``: sessions ranked by tokens, and one session's narrative."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Final
from urllib.parse import quote

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import FIXED_PROJECT
from opik_mcp.read_list.columns import one_line
from opik_mcp.read_list.entities.spend._backend import (
    Row,
    SpendWindow,
    child,
    count,
    link_fields,
    list_header,
    no_usage,
    number,
    page_url,
    refuse_unhonored,
    rows_of,
    spend_client,
    spend_errors,
    spend_window,
    table,
    text,
    tokens,
    whole,
)
from opik_mcp.read_list.handler import Vocabulary
from opik_mcp.read_list.oql import compile_filters, render_filters
from opik_mcp.read_list.paging import clamp_size
from opik_mcp.read_list.sorting import compile_sort

ENTITY = "spend_session"
SUMMARY_CHARS: Final = 120
NARRATED: Final = frozenset({"ready", "partial"})
VOCABULARY = Vocabulary(
    name=ENTITY,
    filter_fields={
        "user_email": "string",
        "turns": "number",
        "total_tokens": "number",
        "duration": "number",
        "start_time": "date_time",
    },
    sort_fields=("total_tokens", "turns", "duration", "start_time", "last_activity"),
    filter_examples=('user_email = "dev@example.com"', "total_tokens > 1000000", "turns >= 50"),
)


def _duration(milliseconds: int) -> str:
    minutes, seconds = divmod(milliseconds // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def _cut(summary: str) -> str:
    flat = one_line(summary)
    return flat if len(flat) <= SUMMARY_CHARS else f"{flat[: SUMMARY_CHARS - 1]}…"


def _backend_filters(clauses: list[dict[str, str]]) -> str | None:
    wire = [
        {"field": clause["field"], "operator": clause["operator"], "value": clause["value"]}
        for clause in clauses
    ]
    return json.dumps(wire, separators=(",", ":")) if wire else None


def _rows(rows: list[Row]) -> str:
    return table(
        ["session", "user", "start", "duration", "turns", "tokens", "analysis", "summary"],
        [
            [
                text(row, "session_id"),
                text(row, "user_email"),
                text(row, "start_time")[:16],
                _duration(whole(row, "duration")),
                count(whole(row, "turns")),
                tokens(number(row, "total_tokens")),
                text(row, "analysis_status"),
                _cut(text(row, "summary")),
            ]
            for row in rows
        ],
    )


async def run_spend_session(
    client: OpikReadClient,
    *,
    filters: str | None = None,
    sort: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
    **unhonored: object,
) -> str:
    refuse_unhonored(
        ENTITY,
        unhonored,
        why="sessions take filters, sort, search, since, until, page and size.",
    )
    clauses = compile_filters(VOCABULARY, filters or "")
    field, direction = compile_sort(VOCABULARY, sort) if sort else ("total_tokens", "DESC")
    window = spend_window(since, until)
    current_page, page_size = max(1, page or 1), clamp_size(size)
    with spend_errors():
        # No user_email in the body: the backend answers 403 to it on this route.
        body = await spend_client(client).list_spend_sessions(
            project_name=FIXED_PROJECT,
            interval_start=window.start,
            interval_end=window.end,
            page=current_page,
            size=page_size,
            filters=_backend_filters(clauses),
            sorting=json.dumps([{"field": field, "direction": direction}], separators=(",", ":")),
            search=search,
        )
    rows = rows_of(body.get("content"))
    total = whole(body, "total") if "total" in body else len(rows)
    pages = max(1, -(-total // page_size))
    order = (
        "by tokens"
        if (field, direction) == ("total_tokens", "DESC")
        else (f"by {field} {direction.lower()}")
    )
    applied = [order, window.label, f"page {current_page}/{pages}", f"{total} sessions"]
    if clauses:
        applied.append(render_filters(VOCABULARY, clauses))
    if search:
        applied.append(f"search {search!r}")
    header = list_header(ENTITY, *applied)
    if not rows:
        narrowed = bool(clauses or search)
        empty = _nothing_here(window, total=total, page=current_page, narrowed=narrowed)
        return f"{header}\n{empty}"
    lines = [header, _rows(rows)]
    if any(len(one_line(text(row, "summary"))) > SUMMARY_CHARS for row in rows):
        lines.append(
            f"Summaries are cut to {SUMMARY_CHARS} characters; "
            "read('spend_session', '<id>') gives the narrative."
        )
    if current_page < pages:
        lines.append(f"{total - current_page * page_size} more sessions: page={current_page + 1}.")
    return "\n".join(lines)


def _nothing_here(window: SpendWindow, *, total: int, page: int, narrowed: bool) -> str:
    if total:
        return (
            f"Page {page} is past the last page; {total} sessions match. "
            f"Go back with list('{ENTITY}', page=1)."
        )
    if narrowed:
        return f"No Claude Code sessions in {window.label} match the filters."
    return no_usage(window)


def _outline_call(session_id: str) -> str:
    return (
        f"list('trace', filters='thread_id = \"{session_id}\" AND name not_contains "
        f"\"automated\"', project_name='{FIXED_PROJECT}', "
        "fields=['name'], sort='start_time asc', size=50)"
    )


def _span_ms(start: str, end: str) -> int | None:
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    return int(delta.total_seconds() * 1000)


def _list_row_facts(body: Row) -> dict[str, object]:
    """What the session's list row shows, so a read never says less than the list."""
    meta = child(body, "session")
    start, end = text(meta, "start_time"), text(meta, "last_activity")
    span = _span_ms(start, end) if start and end else None
    turns = whole(body, "live_turns") or whole(body, "turn_count")
    return {
        "user": text(meta, "user_email") or None,
        "start": start[:16] or None,
        "duration": _duration(span) if span is not None and span >= 0 else None,
        "turns": turns or None,
        "tokens": whole(meta, "total_tokens") or None,
        "summary": text(body, "session_summary") or None,
    }


def _not_ready(session_id: str, body: Row) -> dict[str, object]:
    status = text(body, "status") or "unknown"
    record: dict[str, object] = {
        "session_id": session_id,
        "status": status,
        **_list_row_facts(body),
        "detail": text(body, "failure_detail") or text(body, "failure_code") or None,
        "note": (
            f"No narrative: the session's analysis is {status}, and this server never starts one. "
            f"Outline the turns with {_outline_call(session_id)}."
        ),
    }
    return {key: value for key, value in record.items() if value is not None}


def _narrative(session_id: str, body: Row) -> dict[str, object]:
    meta = child(body, "session")
    tasks = [
        {
            "name": text(task, "name"),
            "summary": text(task, "summary"),
            "turns": whole(task, "turns"),
            "tokens": whole(task, "tokens"),
            "first_trace_id": text(task, "first_trace_id"),
        }
        for task in rows_of(body.get("tasks"))
    ]
    record: dict[str, object] = {
        "session_id": session_id,
        "status": text(body, "status"),
        "user": text(meta, "user_email") or None,
        "model": text(meta, "primary_model") or None,
        "tokens": whole(meta, "total_tokens"),
        "summary": text(body, "session_summary") or None,
        "tasks": tasks,
    }
    if text(body, "status") == "partial":
        record["note"] = "The session grew after it was analysed; later turns are not covered."
    return {key: value for key, value in record.items() if value is not None}


async def fetch_session(
    client: OpikReadClient,
    session_id: str,
    *,
    interval_start: str | None = None,
    interval_end: str | None = None,
) -> dict[str, object]:
    session_id = session_id.strip()
    body = await spend_client(client).get_spend_session_narrative(
        session_id,
        project_name=FIXED_PROJECT,
        interval_start=interval_start,
        interval_end=interval_end,
    )
    if text(body, "status") in NARRATED:
        return _narrative(session_id, body)
    if not isinstance(body.get("session"), dict):
        raise ToolError(
            f"No session '{session_id}' in project {FIXED_PROJECT} for this window. "
            f"list('{ENTITY}') lists sessions by tokens."
        )
    return _not_ready(session_id, body)


def row_link_template(settings: Settings, _project_id: str | None) -> str | None:
    return page_url(settings, "session-analysis/{id}")


def session_links(settings: Settings, data: dict[str, object]) -> dict[str, str]:
    session_id = data.get("session_id")
    if not isinstance(session_id, str):
        return {}
    return link_fields(
        settings,
        f"session-analysis/{quote(session_id, safe='')}",
        "this session on the AI Spend session analysis page",
    )


__all__ = [
    "ENTITY",
    "VOCABULARY",
    "fetch_session",
    "row_link_template",
    "run_spend_session",
    "session_links",
]

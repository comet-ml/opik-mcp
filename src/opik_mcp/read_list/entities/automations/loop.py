"""``loop`` — the project's improvement loop as it runs.

The objects are nodes (the production trace stream, suites, queues, prompts,
judges, CI) and every enabled automation is an edge from the node its trigger
reads to the nodes its steps write, with what it produced over the window.
Derived by the backend on read, so it is one picture and not a collection:
the entity answers ``list`` whole, through its runner.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from opik_mcp.client.protocols import AutomationClient
from opik_mcp.config import Settings
from opik_mcp.read_list.columns import one_line
from opik_mcp.read_list.entities.automations._shared import (
    child,
    count,
    optional_text,
    rows_of,
    text,
)
from opik_mcp.read_list.entities.automations.availability import missing_feature_refused
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.project_scope import require_project_id
from opik_mcp.read_list.ui_links import ProjectArea, project_page_url
from opik_mcp.read_list.unsupported import unsupported_fetch

ENTITY: Final = "loop"

#: The page a node's object has, by node kind. A judge is a score name and a
#: CI node a repository, and neither has a page of its own.
_AREA_OF: Final[Mapping[str, ProjectArea]] = {
    "test_suite": "datasets",
    "dataset": "datasets",
    "annotation_queue": "annotation-queues",
    "prompt": "prompts",
}

#: The arguments the list tool hands every runner that mean nothing here:
#: the loop is one picture of the project, and there is nothing to narrow.
_TOOL_INTERNALS: Final = frozenset({"vocabularies", "settings"})


async def run_loop(
    client: AutomationClient,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    settings: Settings,
    **unhonored: object,
) -> str:
    _refuse_narrowing(unhonored)
    resolved = await require_project_id(
        client, project_id=project_id, project_name=project_name, caller=f"list({ENTITY!r})"
    )
    with missing_feature_refused():
        payload = await client.get_loop(project_id=resolved)
    nodes = rows_of(payload.get("nodes"))
    edges = rows_of(payload.get("edges"))
    window = count(payload, "window_days") or 14
    header = f"[list: {ENTITY} | project {project_name or resolved} | {window}d]"
    if not edges:
        return (
            f"{header}\nFound 0 edges over {len(nodes)} node(s) ({window} days): nothing runs "
            f"on this project yet. list('workflow', project_id='{resolved}') shows what each "
            "recipe would add."
        )
    labels = {text(node, "id"): f"{text(node, 'kind')}:{text(node, 'name')}" for node in nodes}
    lines = [
        header,
        f"Found {len(edges)} edges over {len(nodes)} nodes ({window} days): one edge per "
        "enabled automation, from what its trigger reads to what its steps write.",
        "",
        _table(
            ("node", "kind", "name", "url"),
            [
                (
                    text(node, "id"),
                    text(node, "kind"),
                    text(node, "name"),
                    _node_url(settings, resolved, node) or "",
                )
                for node in nodes
            ],
        ),
        "",
        _table(
            (
                "automation_id",
                "name",
                "recipe",
                "from",
                "to",
                "fired",
                "matched",
                "items_written",
                "steps_failed",
            ),
            [_edge_row(edge, labels) for edge in edges],
        ),
        "",
        "fired, matched, items_written and steps_failed are the window's totals per "
        "automation; read('automation', automation_id) has its definition and "
        "list('automation_run', automation_id=…) its runs.",
    ]
    return "\n".join(lines)


def _refuse_narrowing(given: Mapping[str, object]) -> None:
    """Name every argument the call carried that the loop has no use for."""
    named = sorted(
        name
        for name, value in given.items()
        if name not in _TOOL_INTERNALS and value is not None and value != []
    )
    if named:
        raise EntityArgValidationError(
            f"list({ENTITY!r}) does not take {', '.join(named)}: the loop is one picture of "
            f"the project, with nothing to filter, sort, window or page. Retry "
            f"list({ENTITY!r}, project_id=…) without them."
        )


def _node_url(settings: Settings, project_id: str, node: dict[str, object]) -> str | None:
    area = _AREA_OF.get(text(node, "kind"))
    object_id = optional_text(node, "object_id")
    if area is None or not object_id:
        return None
    return project_page_url(settings, project_id, area, subpath=object_id)


def _edge_row(edge: dict[str, object], labels: Mapping[str, str]) -> tuple[str, ...]:
    produced = child(edge, "produced")
    targets = edge.get("to")
    return (
        text(edge, "automation_id"),
        text(edge, "name"),
        text(edge, "recipe"),
        _label(text(edge, "from"), labels),
        ", ".join(_label(str(target), labels) for target in targets)
        if isinstance(targets, list)
        else "",
        text(produced, "fired"),
        text(produced, "matched"),
        text(produced, "items_written"),
        text(produced, "steps_failed"),
    )


def _label(node_id: str, labels: Mapping[str, str]) -> str:
    """A node as ``<kind>:<name>``, or its id when the nodes list left it out."""
    return labels.get(node_id, node_id)


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    return "\n".join(
        [" | ".join(header), *(" | ".join(one_line(cell) for cell in row) for row in rows)]
    )


HANDLER = EntityHandler(
    entity_type="loop",
    fetch_fn=unsupported_fetch,
    run_fn=run_loop,
    run_verb="draw",
    list_has_name=False,
    list_has_id=False,
    description=(
        "The project's improvement loop as it runs: its objects as nodes (production traces, "
        "suites, queues, prompts, judges, CI) and each enabled automation as an edge from what "
        "its trigger reads to what its steps write, with fired, matched, items_written and "
        "steps_failed over 14 days. A node whose object has a page links it. "
        "List-only, by project; takes no filters, sort, window or page."
    ),
)

__all__ = ["ENTITY", "HANDLER", "run_loop"]

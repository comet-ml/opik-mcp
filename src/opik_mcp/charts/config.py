"""Assembly of a dashboard's stored ``config`` blob.

opik-backend treats ``dashboard.config`` as an opaque ``JsonNode``: it stores
and returns whatever it is handed, and every rule about the shape lives in the
Opik frontend. That makes this module the risky part of the write path — a
config that round-trips through the API happily can still render as an empty
dashboard — so the shapes here are transcribed from the frontend's own
``lib/dashboard`` sources rather than inferred:

- ``version`` is ``DASHBOARD_VERSION`` (4). The UI runs migrations for
  anything lower; emitting the current version means it renders untouched.
- Sections own their widgets AND their layout; the layout is a separate array
  keyed by widget id (``i``), because the grid component takes them apart.
- Placement mirrors ``calculateLayoutForAddingWidget``: first position that
  minimises column height in a 6-column grid, with per-type default sizes.

Everything here is pure — no client, no I/O — so the write tool's ``dry_run``
can show the exact config it would send.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Final

from opik_mcp.charts.spec import ChartSpec

#: Frontend's ``DASHBOARD_VERSION``. Bump only alongside a frontend migration.
DASHBOARD_VERSION: Final = 4

#: Frontend's ``GRID_COLUMNS`` / ``MAX_WIDGET_HEIGHT``.
GRID_COLUMNS: Final = 6
MAX_WIDGET_HEIGHT: Final = 12

DEFAULT_SECTION_TITLE: Final = "Overview"

#: Per-widget-type default box, from ``getWidgetSizeConfig``. Sizes an agent
#: never has to think about: a chart is half a row, a stat card a sixth.
_WIDGET_SIZES: Final[dict[str, dict[str, int]]] = {
    "project_metrics": {"w": 2, "h": 4, "minW": 2, "minH": 4},
    "project_stats_card": {"w": 1, "h": 2, "minW": 1, "minH": 2},
    "text_markdown": {"w": 2, "h": 4, "minW": 1, "minH": 4},
}
_FALLBACK_SIZE: Final[dict[str, int]] = {"w": 2, "h": 2, "minW": 1, "minH": 1}


class DashboardConfigError(ValueError):
    """The stored config is not a shape we can safely edit."""


def new_id() -> str:
    """Short, collision-free id for a section or widget.

    The frontend uses ``uniqid()``; the only contract is uniqueness within the
    dashboard and stability across saves, so a truncated uuid4 does the job
    while staying visibly ours in a config an operator may read.
    """
    return f"mcp{uuid.uuid4().hex[:10]}"


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- layout --------------------------------------------------------------- #


def _size_for(widget_type: str) -> dict[str, int]:
    return _WIDGET_SIZES.get(widget_type, _FALLBACK_SIZE)


def _column_heights(layout: list[dict[str, Any]]) -> list[int]:
    """Bottom edge of each grid column — ``getColumnHeights``."""
    heights = [0] * GRID_COLUMNS
    for item in layout:
        x = int(item.get("x", 0))
        w = int(item.get("w", 1))
        bottom = int(item.get("y", 0)) + int(item.get("h", 1))
        for col in range(max(0, x), min(x + w, GRID_COLUMNS)):
            heights[col] = max(heights[col], bottom)
    return heights


def _first_available_position(w: int, heights: list[int]) -> tuple[int, int]:
    """``findFirstAvailablePosition`` — leftmost x whose span is shallowest."""
    best_x = 0
    best_y: int | None = None
    for x in range(0, GRID_COLUMNS - w + 1):
        top = max(heights[x : x + w])
        if best_y is None or top < best_y:
            best_x, best_y = x, top
    return best_x, 0 if best_y is None else best_y


def layout_item(widget_id: str, widget_type: str, layout: list[dict[str, Any]]) -> dict[str, Any]:
    """Layout entry placing a new widget below/beside what is already there."""
    size = _size_for(widget_type)
    w = min(size["w"], GRID_COLUMNS)
    x, y = (0, 0) if not layout else _first_available_position(w, _column_heights(layout))
    return {
        "i": widget_id,
        "x": x,
        "y": y,
        "w": w,
        "h": size["h"],
        "minW": size["minW"],
        "minH": size["minH"],
        "maxW": GRID_COLUMNS,
        "maxH": MAX_WIDGET_HEIGHT,
    }


# --- building ------------------------------------------------------------- #


def build_section(title: str, charts: list[ChartSpec]) -> dict[str, Any]:
    """One section holding ``charts``, laid out in order."""
    widgets: list[dict[str, Any]] = []
    layout: list[dict[str, Any]] = []
    for chart in charts:
        widget = chart.to_widget(new_id())
        widgets.append(widget)
        layout.append(layout_item(widget["id"], widget["type"], layout))
    return {"id": new_id(), "title": title, "widgets": widgets, "layout": layout}


def build_config(
    charts: list[ChartSpec] | None = None,
    *,
    section_title: str = DEFAULT_SECTION_TITLE,
) -> dict[str, Any]:
    """A complete ``dashboard.config`` holding ``charts`` in one section.

    An empty dashboard still gets its section: that is what the UI's "create
    dashboard" does, and a dashboard with zero sections offers no target to
    drop a widget onto.
    """
    return {
        "version": DASHBOARD_VERSION,
        "sections": [build_section(section_title, list(charts or []))],
        "lastModified": _now_ms(),
    }


# --- editing an existing config ------------------------------------------- #


def _sections_of(config: Any) -> list[dict[str, Any]]:
    if not isinstance(config, dict):
        raise DashboardConfigError(
            "dashboard config is not an object — refusing to edit it. Read the "
            "dashboard and rewrite it with dashboard.create instead."
        )
    sections = config.get("sections")
    if sections is None:
        return []
    if not isinstance(sections, list):
        raise DashboardConfigError("dashboard config.sections is not a list — refusing to edit it.")
    return [s for s in sections if isinstance(s, dict)]


def add_chart(
    config: Any,
    chart: ChartSpec,
    *,
    section: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(new_config, added_widget)`` with ``chart`` appended.

    ``section`` names the target section by title or id; when it is omitted the
    chart lands in the last section, which is where the UI appends too. A named
    section that does not exist is created rather than refused — "put this on a
    new 'Cost' row" is a normal request, and failing it would cost a turn to
    learn a section id nobody has yet.

    Read-modify-write: the backend PATCH replaces ``config`` wholesale, so the
    caller must have just fetched the live config. Every other section, widget
    and layout entry is preserved byte-for-byte.
    """
    # Copy up front so the caller's fetched config is never mutated in place —
    # a failed PATCH must leave the in-memory dashboard as it was read.
    sections = [dict(s) for s in _sections_of(config)]
    widget = chart.to_widget(new_id())

    index: int | None = None
    if section is not None:
        index = next(
            (
                i
                for i, cand in enumerate(sections)
                if cand.get("title") == section or cand.get("id") == section
            ),
            None,
        )
    elif sections:
        index = len(sections) - 1

    if index is None:
        target: dict[str, Any] = {
            "id": new_id(),
            "title": section or DEFAULT_SECTION_TITLE,
            "widgets": [],
            "layout": [],
        }
        sections.append(target)
    else:
        target = sections[index]

    widgets = [w for w in (target.get("widgets") or []) if isinstance(w, dict)]
    layout = [entry for entry in (target.get("layout") or []) if isinstance(entry, dict)]
    target["widgets"] = [*widgets, widget]
    target["layout"] = [*layout, layout_item(widget["id"], widget["type"], layout)]

    new_config = dict(config) if isinstance(config, dict) else {}
    new_config["version"] = DASHBOARD_VERSION
    new_config["sections"] = sections
    new_config["lastModified"] = _now_ms()
    return new_config, widget


def remove_widget(config: Any, widget_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(new_config, removed_widget)`` without ``widget_id``.

    Drops the layout entry alongside the widget — an orphaned layout entry
    leaves a hole in the grid that nothing can fill or remove from the UI.
    """
    sections = [dict(s) for s in _sections_of(config)]
    removed: dict[str, Any] | None = None
    for section in sections:
        widgets = [w for w in (section.get("widgets") or []) if isinstance(w, dict)]
        match = next((w for w in widgets if w.get("id") == widget_id), None)
        if match is None:
            continue
        removed = match
        section["widgets"] = [w for w in widgets if w.get("id") != widget_id]
        section["layout"] = [
            entry
            for entry in (section.get("layout") or [])
            if isinstance(entry, dict) and entry.get("i") != widget_id
        ]
        break

    if removed is None:
        known = ", ".join(w["id"] for _s, w in iter_widgets(config)) or "none"
        raise DashboardConfigError(
            f"no widget {widget_id!r} on this dashboard. Widget ids: {known}"
        )

    new_config = dict(config) if isinstance(config, dict) else {}
    new_config["version"] = DASHBOARD_VERSION
    new_config["sections"] = sections
    new_config["lastModified"] = _now_ms()
    return new_config, removed


# --- reading -------------------------------------------------------------- #


def iter_widgets(config: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """``(section, widget)`` pairs, in dashboard order. Never raises."""
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if not isinstance(config, dict):
        return out
    for section in config.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for widget in section.get("widgets") or []:
            if isinstance(widget, dict) and isinstance(widget.get("id"), str):
                out.append((section, widget))
    return out


def flatten_charts(config: Any) -> list[dict[str, Any]]:
    """The dashboard's charts as a flat, readable list.

    What a reader actually needs is every chart with its section, id, type and
    query — the nesting and the layout geometry are storage detail. Flattening
    on read means an agent can answer "what is on this dashboard" and pass a
    widget id straight to ``chart_data`` or ``dashboard.remove_chart`` without
    walking the config itself.
    """
    charts: list[dict[str, Any]] = []
    for section, widget in iter_widgets(config):
        charts.append(
            {
                "widget_id": widget["id"],
                "section": section.get("title"),
                "section_id": section.get("id"),
                "title": widget.get("title") or widget.get("generatedTitle"),
                "type": widget.get("type"),
                "config": widget.get("config") or {},
            }
        )
    return charts


__all__ = [
    "DASHBOARD_VERSION",
    "DEFAULT_SECTION_TITLE",
    "GRID_COLUMNS",
    "DashboardConfigError",
    "add_chart",
    "build_config",
    "build_section",
    "flatten_charts",
    "iter_widgets",
    "layout_item",
    "new_id",
    "remove_widget",
]

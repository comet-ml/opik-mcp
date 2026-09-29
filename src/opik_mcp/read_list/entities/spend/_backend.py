"""What the five spend entities share: the window, the client, the errors, the text.

Entities may not import each other, so everything two of them need lives here.
Dollars are quoted from the backend and never computed: "billed $" is what an
org pays (seat + over-plan + API), "list $" is the API-rate value of the tokens.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, cast

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.base import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.client.protocols import AiSpendClient, OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import COST_INTELLIGENCE_MODE
from opik_mcp.read_list.columns import one_line
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import ParamField, Vocabulary
from opik_mcp.read_list.oql import compile_filters, render_filters, split_param_clauses
from opik_mcp.read_list.ui_links import link_workspace, opik_ui_base
from opik_mcp.read_list.window import (
    closed_window,
    format_instant,
    parse_bound,
    resolve_window,
    to_minute,
)

MODES: Final = frozenset({COST_INTELLIGENCE_MODE})
DEFAULT_WINDOW_DAYS: Final = 30

Row = Mapping[str, object]


def spend_client(client: OpikReadClient) -> AiSpendClient:
    """The concrete ``OpikClient`` implements both; the handler contract types only one."""
    return cast("AiSpendClient", client)


@contextmanager
def spend_errors() -> Iterator[None]:
    """A failed call reads as the client's own sentence, with no "Failed to …" in front."""
    try:
        yield
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as err:
        raise ToolError(str(err)) from err


# --- window -------------------------------------------------------------- #


@dataclass(frozen=True)
class SpendWindow:
    start: str
    end: str

    @property
    def label(self) -> str:
        return f"{to_minute(parse_bound(self.start))} → {to_minute(parse_bound(self.end))}"


def spend_window(since: str | None, until: str | None) -> SpendWindow:
    """Both ends as instants; a window with no ``since`` is the last 30 days."""
    resolved_since, resolved_until = resolve_window(since=since, until=until)
    start, end = closed_window(resolved_since, resolved_until, days=DEFAULT_WINDOW_DAYS)
    return SpendWindow(format_instant(start), format_instant(end))


# --- arguments ----------------------------------------------------------- #

_TOOL_INTERNALS: Final = frozenset({"vocabularies"})


def refuse_unhonored(entity_type: str, given: Mapping[str, object], *, why: str) -> None:
    """Name every argument the call carried that this type has no use for."""
    named = [
        name
        for name, value in given.items()
        if name not in _TOOL_INTERNALS and value is not None and value != []
    ]
    if named:
        raise EntityArgValidationError(
            f"list({entity_type!r}) does not take {', '.join(sorted(named))}: {why}"
        )


USER_EMAIL: Final = ParamField(
    param="user_email",
    operators=("=",),
    encoding="single",
    why="the backend narrows to one exact address",
)


def user_email_vocabulary(name: str) -> Vocabulary:
    """The one filter the summary, lanes and agents take: a single person."""
    return Vocabulary(
        name=name,
        filter_fields={"user_email": "string"},
        param_fields={"user_email": USER_EMAIL},
        filter_examples=('user_email = "dev@example.com"',),
    )


@dataclass(frozen=True)
class Scope:
    """The person a call is narrowed to, and the filter text to echo."""

    user_email: str | None
    echo: str | None


def user_scope(vocabulary: Vocabulary, filters: str | None) -> Scope:
    if not filters:
        return Scope(None, None)
    clauses = compile_filters(vocabulary, filters)
    _, params = split_param_clauses(vocabulary, clauses)
    return Scope(params.get("user_email"), render_filters(vocabulary, clauses))


# --- links --------------------------------------------------------------- #


def page_url(settings: Settings, page: str) -> str | None:
    """``<ui>/<workspace>/ai-spend/<page>``, or ``None`` when either is unknown."""
    base = opik_ui_base(settings)
    workspace = link_workspace(settings)
    if base is None or workspace is None:
        return None
    return f"{base}/{workspace}/ai-spend/{page}"


def link_line(settings: Settings, page: str, opens: str) -> str | None:
    url = page_url(settings, page)
    return f"Open in Opik: {opens} — {url}" if url is not None else None


def link_fields(settings: Settings, page: str, opens: str) -> dict[str, str]:
    url = page_url(settings, page)
    return {"url": url, "url_opens": opens} if url is not None else {}


# --- reading the backend's JSON ------------------------------------------ #


def number(row: Row, key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def whole(row: Row, key: str) -> int:
    value = number(row, key)
    return int(value) if value is not None else 0


def text(row: Row, key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def rows_of(value: object) -> list[Row]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def child(row: Row, key: str) -> Row:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


# --- rendering ----------------------------------------------------------- #


def usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def billed(row: Row, *keys: str) -> float | None:
    """The sum of the components present, or ``None`` when every one is absent."""
    parts = [value for key in keys if (value := number(row, key)) is not None]
    return sum(parts) if parts else None


def tokens(count: float | int | None) -> str:
    """Three significant figures: a leaderboard is read for order, not the last digit."""
    if count is None:
        return "-"
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(count) >= size:
            return f"{count / size:.3g}{unit}"
    return f"{int(count)}"


def count(value: float | int) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def table(header: Sequence[str], body: Sequence[Sequence[str]]) -> str:
    lines = [" | ".join(header), *(" | ".join(one_line(cell) for cell in row) for row in body)]
    return "\n".join(lines)


def list_header(entity_type: str, *applied: str) -> str:
    """The ``[list: …]`` line; ``with_list_size`` puts the size into it."""
    return f"[list: {entity_type} | {' | '.join(applied)}]"


def no_usage(window: SpendWindow, scope: Scope | None = None) -> str:
    who = f" for {scope.user_email}" if scope is not None and scope.user_email else ""
    return f"No Claude Code usage in {window.label}{who}."


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "MODES",
    "USER_EMAIL",
    "Row",
    "Scope",
    "SpendWindow",
    "billed",
    "child",
    "count",
    "link_fields",
    "link_line",
    "list_header",
    "no_usage",
    "number",
    "page_url",
    "refuse_unhonored",
    "rows_of",
    "spend_client",
    "spend_errors",
    "spend_window",
    "table",
    "text",
    "tokens",
    "usd",
    "user_email_vocabulary",
    "user_scope",
    "whole",
]

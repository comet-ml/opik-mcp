"""``list('spend_user')``: the leaderboard, or who uses one MCP server, skill or tool."""

from __future__ import annotations

import json
from typing import Final, cast, get_args

from opik_mcp.client.ai_spend import SpendItemKind
from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import FIXED_PROJECT
from opik_mcp.read_list.entities.spend._backend import (
    USER_EMAIL,
    Row,
    SpendWindow,
    billed,
    count,
    link_line,
    list_header,
    no_usage,
    number,
    refuse_unhonored,
    rows_of,
    spend_client,
    spend_errors,
    spend_window,
    table,
    text,
    tokens,
    usd,
    whole,
)
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import ParamField, Vocabulary
from opik_mcp.read_list.oql import compile_filters, render_filters, split_param_clauses
from opik_mcp.read_list.paging import clamp_size
from opik_mcp.read_list.sorting import compile_sort

ENTITY = "spend_user"
ROUTING_FIELDS: Final[tuple[str, ...]] = get_args(SpendItemKind)
MAX_ITEM_USERS: Final = 50
_ROUTING_NOTE: Final = (
    "lists who uses that item instead of the leaderboard; one routing filter at a time"
)
VOCABULARY = Vocabulary(
    name=ENTITY,
    filter_fields={"user_email": "string", **dict.fromkeys(ROUTING_FIELDS, "string")},
    param_fields={
        "user_email": USER_EMAIL,
        **{
            name: ParamField(
                param=name,
                operators=("=",),
                encoding="single",
                why="the backend names one item",
            )
            for name in ROUTING_FIELDS
        },
    },
    sort_fields=("total_tokens", "requests", "skills", "mcps", "mcp_calls"),
    filter_examples=('mcp_server = "github"', 'skill = "review"', 'user_email = "dev@example.com"'),
    field_notes=dict.fromkeys(ROUTING_FIELDS, _ROUTING_NOTE),
)
_SORT_WORDS: Final = {"total_tokens": "tokens", "mcp_calls": "MCP calls"}


def _sort_label(field: str, direction: str) -> str:
    word = _SORT_WORDS.get(field, field)
    return (
        f"by {word}"
        if direction == "DESC" and field == "total_tokens"
        else (f"by {word} {direction.lower()}")
    )


async def run_spend_user(
    client: OpikReadClient,
    *,
    name: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    since: str | None = None,
    until: str | None = None,
    page: int | None = None,
    size: int | None = None,
    settings: Settings,
    **unhonored: object,
) -> str:
    refuse_unhonored(
        ENTITY,
        unhonored,
        why="the leaderboard takes name, filters, sort, since, until, page and size.",
    )
    clauses = compile_filters(VOCABULARY, filters or "")
    _, params = split_param_clauses(VOCABULARY, clauses)
    routing = {field: value for field, value in params.items() if field in ROUTING_FIELDS}
    if len(routing) > 1:
        raise EntityArgValidationError(
            f"Name one of {', '.join(ROUTING_FIELDS)} at a time: each lists the users of one item."
        )
    window = spend_window(since, until)
    user_email = params.get("user_email")
    echo = render_filters(VOCABULARY, clauses) if clauses else None
    if routing:
        refuse_unhonored(
            ENTITY,
            {"name": name, "sort": sort, "page": page, "size": size},
            why="the users of one item come back whole, by tokens.",
        )
        return await _item_users(
            client, routing, window, user_email=user_email, echo=echo, settings=settings
        )

    field, direction = compile_sort(VOCABULARY, sort) if sort else ("total_tokens", "DESC")
    current_page, page_size = max(1, page or 1), clamp_size(size)
    with spend_errors():
        body = await spend_client(client).list_spend_users(
            project_name=FIXED_PROJECT,
            interval_start=window.start,
            interval_end=window.end,
            user_email=user_email,
            page=current_page,
            size=page_size,
            name=name,
            sorting=json.dumps([{"field": field, "direction": direction}], separators=(",", ":")),
        )
    rows = rows_of(body.get("content"))
    total = whole(body, "total") if "total" in body else len(rows)
    pages = max(1, -(-total // page_size))
    applied = [
        _sort_label(field, direction),
        window.label,
        f"page {current_page}/{pages}",
        f"{total} users",
    ]
    if name:
        applied.append(f"name {name!r}")
    if echo:
        applied.append(echo)
    header = list_header(ENTITY, *applied)
    if not rows:
        return f"{header}\n{_nothing_here(window, total=total, page=current_page, name=name)}"
    lines = [header, _leaderboard(rows)]
    if current_page < pages:
        lines.append(f"{total - current_page * page_size} more users: page={current_page + 1}.")
    link = link_line(settings, "leaderboard", "the AI Spend leaderboard")
    return "\n".join([*lines, *([link] if link else [])])


def _nothing_here(window: SpendWindow, *, total: int, page: int, name: str | None) -> str:
    if total:
        return f"Page {page} is past the last page; {total} users match."
    if name:
        return f"No Claude Code usage in {window.label} for a user matching {name!r}."
    return no_usage(window)


def _leaderboard(rows: list[Row]) -> str:
    body = [
        [
            text(row, "user_email"),
            text(row, "user_display_name"),
            tokens(number(row, "total_tokens")),
            usd(billed(row, "subscription_cost_usd", "over_plan_cost_usd", "api_cost_usd")),
            usd(number(row, "subscription_cost_usd")),
            usd(number(row, "over_plan_cost_usd")),
            usd(number(row, "api_cost_usd")),
            text(row, "seat_type") or "-",
            count(whole(row, "requests")),
            count(whole(row, "skills")),
            count(whole(row, "mcps")),
            count(whole(row, "mcp_calls")),
        ]
        for row in rows
    ]
    header = [
        "email",
        "name",
        "tokens",
        "billed $",
        "seat $",
        "over-plan $",
        "API $",
        "seat type",
        "requests",
        "skills",
        "MCPs",
        "MCP calls",
    ]
    return table(header, body)


def _item_tokens(row: Row) -> int:
    return whole(row, "definition_tokens") + whole(row, "usage_tokens")


def _calls_cell(kind: str, row: Row) -> str:
    if kind == "skill":
        return f"{whole(row, 'loads')}/{whole(row, 'runs')}"
    return count(whole(row, "calls"))


async def _item_users(
    client: OpikReadClient,
    routing: dict[str, str],
    window: SpendWindow,
    *,
    user_email: str | None,
    echo: str | None,
    settings: Settings,
) -> str:
    ((kind, item),) = routing.items()
    with spend_errors():
        answer = await spend_client(client).list_spend_item_users(
            cast("SpendItemKind", kind),
            item,
            project_name=FIXED_PROJECT,
            interval_start=window.start,
            interval_end=window.end,
            user_email=user_email,
        )
    rows = sorted(
        rows_of(answer), key=lambda row: (_item_tokens(row), whole(row, "calls")), reverse=True
    )
    applied = ["by tokens", window.label, f"{len(rows)} users", *([echo] if echo else [])]
    header = list_header(ENTITY, *applied)
    if not rows:
        return f"{header}\nNo Claude Code usage of {kind} {item!r} in {window.label}."
    calls_column = "loads/runs" if kind == "skill" else "calls"
    body = [
        [
            text(row, "user_email"),
            text(row, "user_display_name"),
            _calls_cell(kind, row),
            tokens(number(row, "definition_tokens")),
            tokens(number(row, "usage_tokens")),
            usd(number(row, "cost_usd")),
            usd(number(row, "cash_cost_usd")) if "cash_cost_usd" in row else "-",
        ]
        for row in rows[:MAX_ITEM_USERS]
    ]
    lines = [
        header,
        table(
            [
                "email",
                "name",
                calls_column,
                "definition tokens",
                "usage tokens",
                "list $",
                "billed $",
            ],
            body,
        ),
    ]
    if len(rows) > MAX_ITEM_USERS:
        lines.append(
            f"{len(rows) - MAX_ITEM_USERS} more users not shown; narrow with "
            "filters='user_email = \"…\"'."
        )
    link = link_line(settings, "home", "the AI Spend home page")
    return "\n".join([*lines, *([link] if link else [])])


__all__ = ["ENTITY", "ROUTING_FIELDS", "VOCABULARY", "run_spend_user"]

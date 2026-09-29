"""``list('spend_summary')``: usage totals and billed dollars for the window."""

from __future__ import annotations

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import FIXED_PROJECT
from opik_mcp.read_list.entities.spend._backend import (
    Row,
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
    usd,
    user_email_vocabulary,
    user_scope,
)

ENTITY = "spend_summary"
VOCABULARY = user_email_vocabulary(ENTITY)


def _dollar_line(body: Row) -> str:
    seat = number(body, "subscription_seat_cost_usd")
    over_plan = number(body, "spend_over_plan_usd")
    api = number(body, "spend_api_usd")
    total = billed(body, "subscription_seat_cost_usd", "spend_over_plan_usd", "spend_api_usd")
    return (
        f"billed {usd(total)} = seat {usd(seat)} + over-plan {usd(over_plan)} + API {usd(api)}"
        f" | list value at API rates {usd(number(body, 'spend_current_usd'))}"
        f" vs {usd(number(body, 'spend_previous_usd'))} in the previous window"
    )


def _is_idle(body: Row) -> bool:
    results = rows_of(body.get("results"))
    return not any((number(row, "current") or 0) > 0 for row in results) and not (
        number(body, "spend_current_usd") or 0
    )


def _cell(row: Row, key: str) -> str:
    value = number(row, key)
    return count(value) if value is not None else "-"


async def run_spend_summary(
    client: OpikReadClient,
    *,
    filters: str | None = None,
    since: str | None = None,
    until: str | None = None,
    settings: Settings,
    **unhonored: object,
) -> str:
    refuse_unhonored(
        ENTITY,
        unhonored,
        why="it is one summary of the window; narrow it with since/until or "
        "filters='user_email = \"…\"'.",
    )
    scope = user_scope(VOCABULARY, filters)
    window = spend_window(since, until)
    with spend_errors():
        body = await spend_client(client).get_spend_summary(
            project_name=FIXED_PROJECT,
            interval_start=window.start,
            interval_end=window.end,
            user_email=scope.user_email,
        )
    header = list_header(ENTITY, window.label, *([scope.echo] if scope.echo else []))
    if _is_idle(body):
        return f"{header}\n{no_usage(window, scope)}"
    metrics = [
        [text(row, "name"), _cell(row, "current"), _cell(row, "previous")]
        for row in rows_of(body.get("results"))
    ]
    lines = [header, table(["metric", "current", "previous"], metrics), _dollar_line(body)]
    link = link_line(settings, "home", "the AI Spend home page")
    return "\n".join([*lines, *([link] if link else [])])


__all__ = ["ENTITY", "VOCABULARY", "run_spend_summary"]

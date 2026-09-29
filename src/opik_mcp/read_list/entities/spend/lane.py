"""``spend_lane``: where tokens went, and one lane's top items."""

from __future__ import annotations

from typing import Final

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import FIXED_PROJECT
from opik_mcp.read_list.entities.spend._backend import (
    Row,
    child,
    link_fields,
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
    user_email_vocabulary,
    user_scope,
    whole,
)
from opik_mcp.read_list.errors import EntityArgValidationError

ENTITY = "spend_lane"
VOCABULARY = user_email_vocabulary(ENTITY)
TOP_ITEMS: Final = 25
LANE_KEYS: Final = (
    "user_prompts",
    "file_attachments",
    "built_in_tools",
    "prior_assistant",
    "skills",
    "custom_agents",
    "mcp_servers",
    "memory",
    "static_overhead",
    "unattributed",
    "thinking",
    "assistant_text",
    "built_in_tool_calls",
    "mcp_tool_calls",
    "skill_invocations",
)
_LEGEND: Final = (
    "list_usd is the API-rate value of the tokens; billed_usd is cash spend "
    "(seat + over-plan + API)"
)


def _dollars(row: Row, key: str) -> float | None:
    value = number(row, key)
    return None if value is None else round(value, 2)


def _by_list_value(row: Row) -> tuple[float, float]:
    cost = number(row, "cost_usd")
    return (-1.0 if cost is None else cost, number(row, "total_tokens") or 0.0)


def _side_rows(side_name: str, side: Row) -> tuple[list[list[str]], str]:
    side_cost = number(side, "cost_usd")
    body = []
    for lane in sorted(rows_of(side.get("lanes")), key=_by_list_value, reverse=True):
        cost = number(lane, "cost_usd")
        share = f"{cost / side_cost:.0%}" if cost is not None and side_cost else "-"
        body.append(
            [
                side_name,
                text(lane, "key"),
                text(lane, "label"),
                tokens(number(lane, "total_tokens")),
                usd(cost),
                share,
            ]
        )
    total = f"{side_name} total {tokens(number(side, 'total_tokens'))} tokens, {usd(side_cost)}"
    return body, total


async def run_spend_lane(
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
        why="the lanes are a fixed list for the window; narrow them with since/until or "
        "filters='user_email = \"…\"', or read one lane for its items.",
    )
    scope = user_scope(VOCABULARY, filters)
    window = spend_window(since, until)
    with spend_errors():
        body = await spend_client(client).get_spend_composition(
            project_name=FIXED_PROJECT,
            interval_start=window.start,
            interval_end=window.end,
            user_email=scope.user_email,
        )
    header = list_header(
        ENTITY, "by list $ within each side", window.label, *([scope.echo] if scope.echo else [])
    )
    input_side, output_side = child(body, "input"), child(body, "output")
    if not whole(input_side, "total_tokens") and not whole(output_side, "total_tokens"):
        return f"{header}\n{no_usage(window, scope)}"
    input_rows, input_total = _side_rows("input", input_side)
    output_rows, output_total = _side_rows("output", output_side)
    lines = [
        header,
        table(["side", "key", "label", "tokens", "list $", "share"], input_rows + output_rows),
        f"{input_total} | {output_total}",
        "read('spend_lane', '<key>') lists a lane's top items.",
    ]
    link = link_line(settings, "home", "the AI Spend home page")
    return "\n".join([*lines, *([link] if link else [])])


def _item(row: Row) -> dict[str, object]:
    item: dict[str, object] = {
        "label": text(row, "label"),
        "count": whole(row, "count"),
        "tokens": whole(row, "total_tokens"),
        "definition_tokens": whole(row, "definition_tokens"),
        "usage_tokens": whole(row, "usage_tokens"),
        "list_usd": _dollars(row, "cost_usd"),
        "billed_usd": _dollars(row, "cash_cost_usd"),
        "recoverable_usd": _dollars(row, "recoverable_cost_usd"),
        "active_users": row.get("active_users"),
        "inactive_users": row.get("inactive_users"),
    }
    return {key: value for key, value in item.items() if value is not None}


def _check_lane_key(lane_key: str) -> None:
    if lane_key == "unattributed":
        raise EntityArgValidationError(
            "The 'unattributed' lane has no breakdown: it is the tokens no other lane "
            "claimed. Its size is in list('spend_lane')."
        )
    if lane_key not in LANE_KEYS:
        raise EntityArgValidationError(
            f"Unknown lane {lane_key!r}. Lanes: {', '.join(LANE_KEYS)}. "
            "list('spend_lane') shows their sizes."
        )


async def fetch_lane(
    client: OpikReadClient,
    lane_key: str,
    *,
    interval_start: str | None = None,
    interval_end: str | None = None,
) -> dict[str, object]:
    lane_key = lane_key.strip()
    _check_lane_key(lane_key)
    window = spend_window(interval_start, interval_end)
    body = await spend_client(client).get_spend_lane_breakdown(
        lane_key,
        project_name=FIXED_PROJECT,
        interval_start=window.start,
        interval_end=window.end,
    )
    items = sorted(rows_of(body.get("items")), key=_by_list_value, reverse=True)
    item_count = whole(body, "item_count") or len(items)
    record: dict[str, object] = {
        "lane": lane_key,
        "title": text(body, "title"),
        "subtitle": text(body, "subtitle") or None,
        "window": window.label,
        "tokens": whole(body, "total_tokens"),
        "list_usd": _dollars(body, "cost_usd"),
        "billed_usd": _dollars(body, "cash_cost_usd"),
        "over_plan_usd": _dollars(body, "over_plan_cost_usd"),
        "api_usd": _dollars(body, "api_cost_usd"),
        "legend": _LEGEND,
        "item_unit": text(body, "item_unit") or None,
        "items": [_item(row) for row in items[:TOP_ITEMS]],
    }
    hidden = item_count - min(len(items), TOP_ITEMS)
    if hidden > 0:
        record["more"] = f"{hidden} more items not shown: the top {TOP_ITEMS} by list $ are above."
    return {key: value for key, value in record.items() if value is not None}


def lane_links(settings: Settings, _data: dict[str, object]) -> dict[str, str]:
    return link_fields(settings, "home", "the AI Spend home page")


__all__ = ["ENTITY", "LANE_KEYS", "VOCABULARY", "fetch_lane", "lane_links", "run_spend_lane"]

"""``list('spend_agent')``: what each subagent cost, and how much of the window can say."""

from __future__ import annotations

from opik_mcp.client.protocols import AiSpendClient
from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import FIXED_PROJECT
from opik_mcp.read_list.entities.spend._backend import (
    count,
    link_line,
    list_header,
    no_usage,
    number,
    refuse_unhonored,
    rows_of,
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

ENTITY = "spend_agent"
VOCABULARY = user_email_vocabulary(ENTITY)


async def run_spend_agent(
    client: AiSpendClient,
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
        why="agents are one ranked list for the window; narrow it with since/until or "
        "filters='user_email = \"…\"'.",
    )
    scope = user_scope(VOCABULARY, filters)
    window = spend_window(since, until)
    with spend_errors():
        body = await client.get_spend_agents(
            project_name=FIXED_PROJECT,
            interval_start=window.start,
            interval_end=window.end,
            user_email=scope.user_email,
        )
    header = list_header(ENTITY, "by tokens", window.label, *([scope.echo] if scope.echo else []))
    if body.get("available") is False:
        reason = text(body, "unavailable_reason") or text(body, "unavailable_code")
        return f"{header}\nSubagent spend is unavailable: {reason}"
    coverage = body.get("coverage")
    window_calls = whole(coverage, "window_calls") if isinstance(coverage, dict) else 0
    agents = sorted(
        rows_of(body.get("agents")), key=lambda row: whole(row, "total_tokens"), reverse=True
    )
    if not agents:
        if not window_calls:
            return f"{header}\n{no_usage(window, scope)}"
        return (
            f"{header}\nNo subagent runs in {window.label} ({count(window_calls)} calls checked)."
        )
    lines = [
        header,
        table(
            ["agent", "calls", "invocations", "tokens", "list $"],
            [
                [
                    text(row, "label"),
                    count(number(row, "calls")),
                    count(number(row, "invocations")),
                    tokens(number(row, "total_tokens")),
                    usd(number(row, "cost_usd")),
                ]
                for row in agents
            ],
        ),
        f"total {tokens(number(body, 'total_tokens'))} tokens, {usd(number(body, 'cost_usd'))}",
    ]
    unattributed = whole(body, "unattributed_calls")
    if unattributed:
        lines.append(
            f"unattributed: {tokens(number(body, 'unattributed_tokens'))} tokens, "
            f"{count(unattributed)} calls the backend could not name."
        )
    ratio = number(coverage, "labelled_ratio") if isinstance(coverage, dict) else None
    if ratio is not None and ratio < 1:
        lines.append(
            f"coverage: {ratio:.0%} of {count(window_calls)} calls carry an agent label, "
            "so these rows are a floor."
        )
    link = link_line(settings, "home", "the AI Spend home page")
    return "\n".join([*lines, *([link] if link else [])])


__all__ = ["ENTITY", "VOCABULARY", "run_spend_agent"]

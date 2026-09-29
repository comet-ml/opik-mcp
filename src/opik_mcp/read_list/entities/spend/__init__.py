"""AI Spend: summary, lanes, users, sessions and agents.

Five entities in one namespace because they share the window, the client and
the dollar labels, and entities may not import each other. All exist only in an
AI Spend workspace and none belongs to a project (the backend is asked for the
fixed one). Lists answer through ``run_fn``; a lane and a session also read.
"""

from __future__ import annotations

from opik_mcp.cost_intelligence import AI_SPEND_FEATURE
from opik_mcp.read_list.entities.spend.agent import VOCABULARY as AGENT_VOCABULARY
from opik_mcp.read_list.entities.spend.agent import run_spend_agent
from opik_mcp.read_list.entities.spend.lane import VOCABULARY as LANE_VOCABULARY
from opik_mcp.read_list.entities.spend.lane import fetch_lane, lane_links, run_spend_lane
from opik_mcp.read_list.entities.spend.session import VOCABULARY as SESSION_VOCABULARY
from opik_mcp.read_list.entities.spend.session import (
    fetch_session,
    run_spend_session,
    session_links,
)
from opik_mcp.read_list.entities.spend.session import row_link_template as session_row_link
from opik_mcp.read_list.entities.spend.summary import VOCABULARY as SUMMARY_VOCABULARY
from opik_mcp.read_list.entities.spend.summary import run_spend_summary
from opik_mcp.read_list.entities.spend.user import VOCABULARY as USER_VOCABULARY
from opik_mcp.read_list.entities.spend.user import leaderboard_note, run_spend_user
from opik_mcp.read_list.handler import EntityHandler, ReadWindow
from opik_mcp.read_list.unsupported import unsupported_fetch

_WINDOW = ReadWindow(start_kwarg="interval_start", end_kwarg="interval_end")


def _hint(entity_type: str) -> str:
    return (
        f"Opik did not answer in time for list({entity_type!r}, …). "
        "Narrow the window with since/until and retry."
    )


SUMMARY_HANDLER = EntityHandler(
    entity_type="spend_summary",
    feature=AI_SPEND_FEATURE,
    fetch_fn=unsupported_fetch,
    run_fn=run_spend_summary,
    run_takes_window=True,
    run_timeout_hint=_hint("spend_summary"),
    vocabularies=(SUMMARY_VOCABULARY,),
    description=(
        "Claude Code usage totals for the window against the window before it, with "
        "billed dollars split into seat, over-plan and API."
    ),
)

LANE_HANDLER = EntityHandler(
    entity_type="spend_lane",
    feature=AI_SPEND_FEATURE,
    fetch_fn=fetch_lane,
    id_only=True,
    read_window=_WINDOW,
    link_fn=lane_links,
    run_fn=run_spend_lane,
    run_takes_window=True,
    run_timeout_hint=_hint("spend_lane"),
    vocabularies=(LANE_VOCABULARY,),
    description=(
        "Where input and output tokens went, one row per lane with tokens and list-price "
        "dollars; reading a lane returns its top 25 items by list-price dollars."
    ),
)

USER_HANDLER = EntityHandler(
    entity_type="spend_user",
    feature=AI_SPEND_FEATURE,
    fetch_fn=unsupported_fetch,
    run_fn=run_spend_user,
    run_takes_window=True,
    page_note_fn=leaderboard_note,
    run_timeout_hint=_hint("spend_user"),
    vocabularies=(USER_VOCABULARY,),
    description=(
        "The per-user leaderboard by total tokens, with billed dollars; a filter on "
        "mcp_server, skill or built_in_tool lists the users of that item instead."
    ),
)

SESSION_HANDLER = EntityHandler(
    entity_type="spend_session",
    feature=AI_SPEND_FEATURE,
    fetch_fn=fetch_session,
    id_only=True,
    read_window=_WINDOW,
    link_fn=session_links,
    run_fn=run_spend_session,
    run_takes_window=True,
    run_takes_search=True,
    row_link_template=session_row_link,
    run_timeout_hint=_hint("spend_session"),
    vocabularies=(SESSION_VOCABULARY,),
    description=(
        "Claude Code sessions by total tokens; reading one returns its analysed "
        "narrative, or names the call that outlines it when no analysis is ready."
    ),
)

AGENT_HANDLER = EntityHandler(
    entity_type="spend_agent",
    feature=AI_SPEND_FEATURE,
    fetch_fn=unsupported_fetch,
    run_fn=run_spend_agent,
    run_takes_window=True,
    run_timeout_hint=_hint("spend_agent"),
    vocabularies=(AGENT_VOCABULARY,),
    description=(
        "Per-subagent calls, tokens and list-price dollars, with the unattributed "
        "share and how much of the window carries an agent label."
    ),
)

HANDLERS = (SUMMARY_HANDLER, LANE_HANDLER, USER_HANDLER, SESSION_HANDLER, AGENT_HANDLER)

__all__ = ["HANDLERS"]

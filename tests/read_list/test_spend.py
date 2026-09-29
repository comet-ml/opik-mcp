"""The five AI Spend entities, through ``list`` and ``read`` in an AI Spend workspace."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from typing import cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.ai_spend import SpendAdminRequiredError, SpendItemKind
from opik_mcp.client.protocols import OpikListClient, OpikReadClient
from opik_mcp.cost_intelligence import AI_SPEND_FEATURE, FIXED_PROJECT, enabled_features
from opik_mcp.cost_intelligence.descriptions import GUIDE_NAME
from opik_mcp.read_list import registry
from opik_mcp.read_list.entities.spend.lane import LANE_KEYS, TOP_ITEMS
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.paging import DEFAULT_PAGE_SIZE
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.reference import LIST_SCHEMA_KEYS
from opik_mcp.read_list.visibility import (
    added_listable,
    added_readable,
    added_schema_keys,
    listable_types,
    readable_types,
)
from opik_mcp.read_list.window import parse_bound
from opik_mcp.skills_catalog import run_read_skill
from opik_mcp.writes.schema_tool import run_schema
from tests.factories import make_settings

pytestmark = pytest.mark.anyio

SPEND = make_settings(
    comet_workspace="__ai_spend_test__",
    opik_mcp_transport="stdio",
    opik_api_key="k",
    opik_url="https://opik.example.com/api",
)
DEFAULT = make_settings(comet_workspace="team", opik_api_key="k")
SPEND_TYPES = ("spend_summary", "spend_lane", "spend_user", "spend_session", "spend_agent")
ADMIN_SENTENCE = "Spend data needs an organization admin's API key for this workspace (403)."
SESSION = "5d1c6a52-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeSpend:
    """Answers the spend endpoints and records the keyword arguments of each call."""

    summary: dict[str, object] = field(default_factory=dict)
    composition: dict[str, object] = field(default_factory=dict)
    breakdown: dict[str, object] = field(default_factory=dict)
    users: dict[str, object] = field(default_factory=lambda: {"total": 0, "content": []})
    item_users: list[object] = field(default_factory=list)
    sessions: dict[str, object] = field(default_factory=lambda: {"total": 0, "content": []})
    narrative: dict[str, object] = field(default_factory=dict)
    agents: dict[str, object] = field(default_factory=dict)
    error: Exception | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def _record(self, call: str, /, **kwargs: object) -> None:
        self.calls.append((call, kwargs))
        if self.error is not None:
            raise self.error

    @property
    def last(self) -> dict[str, object]:
        return self.calls[-1][1]

    async def get_spend_summary(self, **kwargs: object) -> dict[str, object]:
        self._record("summary", **kwargs)
        return self.summary

    async def get_spend_composition(self, **kwargs: object) -> dict[str, object]:
        self._record("composition", **kwargs)
        return self.composition

    async def get_spend_lane_breakdown(self, lane_key: str, **kwargs: object) -> dict[str, object]:
        self._record("breakdown", lane_key=lane_key, **kwargs)
        return self.breakdown

    async def list_spend_users(self, **kwargs: object) -> dict[str, object]:
        self._record("users", **kwargs)
        return self.users

    async def list_spend_item_users(
        self, kind: SpendItemKind, item: str, **kwargs: object
    ) -> list[object]:
        self._record("item_users", kind=kind, item=item, **kwargs)
        return self.item_users

    async def list_spend_sessions(self, **kwargs: object) -> dict[str, object]:
        self._record("sessions", **kwargs)
        return self.sessions

    async def get_spend_session_narrative(
        self, session_id: str, **kwargs: object
    ) -> dict[str, object]:
        self._record("narrative", session_id=session_id, **kwargs)
        return self.narrative

    async def get_spend_agents(self, **kwargs: object) -> dict[str, object]:
        self._record("agents", **kwargs)
        return self.agents


async def _list(
    fake: FakeSpend,
    entity_type: str,
    *,
    filters: str | None = None,
    sort: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    fields: list[str] | None = None,
    name: str | None = None,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
    project_name: str | None = None,
) -> str:
    return await run_list(
        entity_type,
        filters=filters,
        sort=sort,
        since=since,
        until=until,
        search=search,
        fields=fields,
        name=name,
        page=page,
        size=size,
        project_name=project_name,
        settings=SPEND,
        client=cast("OpikListClient", fake),
    )


async def _read(
    fake: FakeSpend, entity_type: str, record_id: str, *, since: str | None = None
) -> str:
    return await run_read(
        entity_type,
        record_id,
        since=since,
        settings=SPEND,
        client=cast("OpikReadClient", fake),
    )


def _user_row(email: str, tokens: int) -> dict[str, object]:
    return {
        "user_email": email,
        "user_display_name": "Dev One",
        "total_tokens": tokens,
        "subscription_cost_usd": 20.0,
        "over_plan_cost_usd": 5.5,
        "api_cost_usd": 1.25,
        "seat_type": "Premium",
        "requests": 40,
        "skills": 3,
        "mcps": 2,
        "mcp_calls": 17,
    }


def _session_row(session_id: str, summary: str = "Refactored the parser.") -> dict[str, object]:
    return {
        "session_id": session_id,
        "user_email": "dev@example.com",
        "start_time": "2026-09-20T10:00:00Z",
        "duration": 4_320_000,
        "turns": 12,
        "total_tokens": 2_500_000,
        "analysis_status": "ready",
        "summary": summary,
    }


def _first_line(answer: str) -> str:
    return answer.split("\n", 1)[0]


# --- window and project ---------------------------------------------------- #


async def test_a_window_with_no_since_is_the_last_30_days() -> None:
    fake = FakeSpend(summary={"results": [{"name": "total_messages", "current": 3}]})
    await _list(fake, "spend_summary")
    start, end = (
        parse_bound(str(fake.last["interval_start"])),
        parse_bound(str(fake.last["interval_end"])),
    )
    assert end - start == timedelta(days=30)


async def test_since_and_until_are_the_backends_interval() -> None:
    fake = FakeSpend()
    await _list(fake, "spend_agent", since="2026-09-01T00:00:00Z", until="2026-09-08T00:00:00Z")
    assert fake.last["interval_start"] == "2026-09-01T00:00:00Z"
    assert fake.last["interval_end"] == "2026-09-08T00:00:00Z"


async def test_every_spend_call_asks_for_the_fixed_project() -> None:
    fake = FakeSpend(
        breakdown={"title": "t", "items": []},
        narrative={"status": "ready"},
    )
    for entity_type in (
        "spend_summary",
        "spend_lane",
        "spend_user",
        "spend_session",
        "spend_agent",
    ):
        await _list(fake, entity_type)
    await _list(fake, "spend_user", filters='skill = "x"')
    await _read(fake, "spend_lane", "skills")
    await _read(fake, "spend_session", SESSION)
    assert len(fake.calls) == 8
    assert {call["project_name"] for _, call in fake.calls} == {FIXED_PROJECT}


async def test_a_read_window_is_forwarded_to_the_lane_and_defaults_to_30_days() -> None:
    fake = FakeSpend(breakdown={"title": "Skills", "items": []})
    await _read(fake, "spend_lane", "skills", since="2026-09-01T00:00:00Z")
    assert fake.last["interval_start"] == "2026-09-01T00:00:00Z"
    await _read(fake, "spend_lane", "skills")
    start, end = (
        parse_bound(str(fake.last["interval_start"])),
        parse_bound(str(fake.last["interval_end"])),
    )
    assert end - start == timedelta(days=30)


# --- leaderboard ----------------------------------------------------------- #


async def test_the_leaderboard_is_sorted_by_total_tokens_unless_asked_otherwise() -> None:
    fake = FakeSpend(users={"total": 1, "content": [_user_row("a@example.com", 5_000_000)]})
    await _list(fake, "spend_user")
    assert json.loads(str(fake.last["sorting"])) == [{"field": "total_tokens", "direction": "DESC"}]
    await _list(fake, "spend_user", sort="requests asc", page=2, size=5, name="dev")
    assert json.loads(str(fake.last["sorting"])) == [{"field": "requests", "direction": "ASC"}]
    assert (fake.last["page"], fake.last["size"], fake.last["name"]) == (2, 5, "dev")


async def test_the_leaderboard_says_billed_dollars_and_its_ranking_by_tokens() -> None:
    fake = FakeSpend(users={"total": 1, "content": [_user_row("a@example.com", 5_000_000)]})
    answer = await _list(fake, "spend_user")
    assert "by tokens" in _first_line(answer)
    rows = answer.split("\n")
    assert rows[1].split(" | ")[:4] == ["email", "name", "tokens", "billed $"]
    assert rows[2].startswith("a@example.com | Dev One | 5M | $26.75 | $20.00 | $5.50 | $1.25")


async def test_an_unknown_sort_field_is_refused_with_the_valid_ones() -> None:
    with pytest.raises(ToolError, match="total_tokens, requests, skills, mcps, mcp_calls"):
        await _list(FakeSpend(), "spend_user", sort="cost desc")


@pytest.mark.parametrize(
    ("field_name", "kind"),
    [("mcp_server", "mcp_server"), ("skill", "skill"), ("built_in_tool", "built_in_tool")],
)
async def test_a_routing_filter_lists_the_users_of_that_item(field_name: str, kind: str) -> None:
    fake = FakeSpend(
        item_users=[
            {
                "user_email": "low@example.com",
                "calls": 1,
                "loads": 1,
                "runs": 1,
                "definition_tokens": 10,
                "usage_tokens": 5,
                "cost_usd": 0.1,
                "cash_cost_usd": 0.05,
            },
            {
                "user_email": "high@example.com",
                "calls": 9,
                "loads": 4,
                "runs": 2,
                "definition_tokens": 1000,
                "usage_tokens": 5000,
                "cost_usd": 3.0,
                "cash_cost_usd": 1.5,
            },
        ]
    )
    answer = await _list(fake, "spend_user", filters=f'{field_name} = "the-item"')
    assert fake.calls[-1][0] == "item_users"
    assert (fake.last["kind"], fake.last["item"]) == (kind, "the-item")
    lines = answer.split("\n")
    assert "list $ | billed $" in lines[1]
    assert lines[2].startswith("high@example.com")
    assert lines[2].endswith("$3.00 | $1.50")


async def test_two_routing_filters_or_paging_a_routing_answer_are_refused() -> None:
    fake = FakeSpend()
    with pytest.raises(ToolError, match="one of mcp_server, skill, built_in_tool at a time"):
        await _list(fake, "spend_user", filters='skill = "a" AND mcp_server = "b"')
    with pytest.raises(ToolError, match="does not take page"):
        await _list(fake, "spend_user", filters='skill = "a"', page=2)
    assert fake.calls == []


async def test_the_users_of_an_item_are_capped_and_the_cut_is_stated() -> None:
    fake = FakeSpend(
        item_users=[
            {"user_email": f"u{i}@example.com", "calls": i, "definition_tokens": i}
            for i in range(60)
        ]
    )
    answer = await _list(fake, "spend_user", filters='mcp_server = "x"')
    assert "10 more users not shown" in answer
    assert "60 users" in _first_line(answer)


async def test_the_leaderboard_states_how_many_pages_are_left() -> None:
    fake = FakeSpend(users={"total": 27, "content": [_user_row("a@example.com", 1)]})
    answer = await _list(fake, "spend_user", size=10)
    assert "page 1/3" in _first_line(answer)
    assert "17 more users: page=2." in answer


# --- sessions -------------------------------------------------------------- #


async def test_session_filters_compile_to_the_backends_json_and_sort_defaults_to_tokens() -> None:
    fake = FakeSpend(sessions={"total": 1, "content": [_session_row(SESSION)]})
    await _list(
        fake,
        "spend_session",
        filters='user_email = "dev@example.com" AND turns >= 10 AND total_tokens > 1000',
    )
    assert json.loads(str(fake.last["filters"])) == [
        {"field": "user_email", "operator": "=", "value": "dev@example.com"},
        {"field": "turns", "operator": ">=", "value": "10"},
        {"field": "total_tokens", "operator": ">", "value": "1000"},
    ]
    assert json.loads(str(fake.last["sorting"])) == [{"field": "total_tokens", "direction": "DESC"}]


async def test_sessions_never_send_user_email_in_the_body() -> None:
    fake = FakeSpend(sessions={"total": 0, "content": []})
    await _list(fake, "spend_session", filters='user_email = "dev@example.com"')
    assert "user_email" not in fake.last


async def test_a_session_row_cuts_its_summary_and_carries_the_id_to_read() -> None:
    fake = FakeSpend(
        sessions={"total": 1, "content": [_session_row(SESSION, summary="word " * 100)]}
    )
    answer = await _list(fake, "spend_session")
    row = answer.split("\n")[2].split(" | ")
    assert row[0] == SESSION
    assert row[3] == "1h12m"
    assert len(row[-1]) == 120
    assert row[-1].endswith("…")


async def test_session_filters_take_only_the_declared_fields() -> None:
    with pytest.raises(ToolError) as exc:
        await _list(FakeSpend(), "spend_session", filters='harness = "x"')
    for name in ("user_email", "turns", "total_tokens", "duration", "start_time"):
        assert name in str(exc.value)


async def test_a_session_read_with_a_ready_analysis_returns_the_narrative() -> None:
    fake = FakeSpend(
        narrative={
            "status": "ready",
            "session": {
                "user_email": "dev@example.com",
                "primary_model": "claude-opus",
                "total_tokens": 900,
            },
            "session_summary": "Fixed the flaky test.",
            "tasks": [
                {
                    "name": "Reproduce",
                    "summary": "Ran it 50 times.",
                    "turns": 4,
                    "tokens": 300,
                    "first_trace_id": "t-1",
                }
            ],
        }
    )
    answer = await _read(fake, "spend_session", SESSION)
    header, payload = answer.split("\n", 1)
    assert re.match(rf"\[read: spend_session {SESSION} \| [\d,]+ tok", header)
    data = json.loads(payload)
    assert data["user"] == "dev@example.com"
    assert data["model"] == "claude-opus"
    assert data["tasks"][0]["first_trace_id"] == "t-1"
    assert data["url"].endswith(f"/__ai_spend_test__/ai-spend/session-analysis/{SESSION}")


@pytest.mark.parametrize("status", ["running", "not_started", "failed", "skipped"])
async def test_a_session_read_without_an_analysis_names_the_outline_call(status: str) -> None:
    fake = FakeSpend(narrative={"status": status, "failure_detail": "model timed out"})
    data = json.loads((await _read(fake, "spend_session", SESSION)).split("\n", 1)[1])
    assert data["status"] == status
    assert (
        f"list('trace', filters='thread_id = \"{SESSION}\" AND name not_contains \"automated\"', "
        "fields=['name'], sort='start_time asc', size=50)" in data["note"]
    )
    assert data.get("detail") == "model timed out"
    assert "tasks" not in data


# --- summary, lanes, agents ------------------------------------------------ #


async def test_the_summary_splits_billed_dollars_and_labels_list_dollars() -> None:
    fake = FakeSpend(
        summary={
            "results": [{"name": "total_messages", "current": 120.0, "previous": 100.0}],
            "spend_current_usd": 400.0,
            "spend_previous_usd": 300.0,
            "subscription_seat_cost_usd": 40.0,
            "spend_over_plan_usd": 10.0,
            "spend_api_usd": 5.0,
        }
    )
    answer = await _list(fake, "spend_summary")
    assert "total_messages | 120 | 100" in answer
    assert "billed $55.00 = seat $40.00 + over-plan $10.00 + API $5.00" in answer
    assert "list value at API rates $400.00 vs $300.00" in answer
    assert "/__ai_spend_test__/ai-spend/home" in answer


async def test_lanes_are_sorted_by_list_dollars_within_each_side() -> None:
    fake = FakeSpend(
        composition={
            "input": {
                "total_tokens": 300,
                "cost_usd": 10.0,
                "lanes": [
                    {"key": "memory", "label": "Memory", "total_tokens": 250, "cost_usd": 1.0},
                    {"key": "skills", "label": "Skills", "total_tokens": 50, "cost_usd": 9.0},
                ],
            },
            "output": {"total_tokens": 10, "cost_usd": 2.0, "lanes": []},
        }
    )
    answer = await _list(fake, "spend_lane")
    rows = answer.split("\n")
    assert rows[1] == "side | key | label | tokens | list $ | share"
    assert rows[2] == "input | skills | Skills | 50 | $9.00 | 90%"
    assert rows[3].startswith("input | memory")
    assert "input total 300 tokens, $10.00 | output total 10 tokens, $2.00" in answer
    assert "breakdowns" not in answer


async def test_the_lane_list_is_small() -> None:
    lanes = [
        {"key": key, "label": key.title(), "total_tokens": 1_000_000, "cost_usd": 12.5}
        for key in LANE_KEYS
    ]
    fake = FakeSpend(
        composition={
            "input": {"total_tokens": 9, "cost_usd": 1.0, "lanes": lanes[:10]},
            "output": {"total_tokens": 9, "cost_usd": 1.0, "lanes": lanes[10:]},
        }
    )
    answer = await _list(fake, "spend_lane")
    matched = re.search(r"([\d,]+) tok", _first_line(answer))
    assert matched is not None
    assert int(matched.group(1).replace(",", "")) < 2000


async def test_a_lane_read_rounds_dollars_to_cents_and_keeps_tokens_exact() -> None:
    fake = FakeSpend(
        breakdown={
            "total_tokens": 123456789,
            "cost_usd": 27.165666499999997,
            "cash_cost_usd": 1.005,
            "over_plan_cost_usd": 2.3333333,
            "api_cost_usd": 0.1 + 0.2,
            "items": [
                {"label": "a", "total_tokens": 7, "cost_usd": 1.23456, "cash_cost_usd": 0.999}
            ],
        }
    )
    data = json.loads((await _read(fake, "spend_lane", "mcp_servers")).split("\n", 1)[1])
    assert (data["list_usd"], data["over_plan_usd"], data["api_usd"]) == (27.17, 2.33, 0.3)
    assert (data["items"][0]["list_usd"], data["items"][0]["billed_usd"]) == (1.23, 1.0)
    assert data["tokens"] == 123456789


async def test_a_lane_read_keeps_the_top_25_items_and_counts_the_rest() -> None:
    items = [
        {"label": f"item-{i}", "count": i, "total_tokens": i, "cost_usd": float(i)}
        for i in range(1, 41)
    ]
    fake = FakeSpend(
        breakdown={
            "title": "MCP servers",
            "subtitle": "Server definitions.",
            "total_tokens": 100,
            "cost_usd": 820.0,
            "cash_cost_usd": 7.0,
            "over_plan_cost_usd": 2.0,
            "api_cost_usd": 3.0,
            "item_count": 40,
            "items": items,
        }
    )
    answer = await _read(fake, "spend_lane", "mcp_servers")
    data = json.loads(answer.split("\n", 1)[1])
    assert [item["label"] for item in data["items"]][:2] == ["item-40", "item-39"]
    assert len(data["items"]) == TOP_ITEMS
    assert "15 more items" in data["more"]
    assert (data["list_usd"], data["billed_usd"]) == (820.0, 7.0)
    assert "billed_usd" in data["legend"]
    assert data["url"].endswith("/ai-spend/home")


async def test_the_unattributed_lane_and_unknown_lanes_are_refused_before_the_backend() -> None:
    fake = FakeSpend()
    with pytest.raises(ToolError, match="'unattributed' lane has no breakdown"):
        await _read(fake, "spend_lane", "unattributed")
    with pytest.raises(ToolError) as exc:
        await _read(fake, "spend_lane", "nope")
    assert all(key in str(exc.value) for key in LANE_KEYS)
    assert fake.calls == []


async def test_the_agent_table_says_unattributed_and_coverage() -> None:
    fake = FakeSpend(
        agents={
            "agents": [
                {"label": "small", "calls": 2, "invocations": 1, "total_tokens": 10},
                {
                    "label": "reviewer",
                    "calls": 30,
                    "invocations": 5,
                    "total_tokens": 9_000,
                    "cost_usd": 4.5,
                },
            ],
            "total_tokens": 9_010,
            "cost_usd": 4.6,
            "unattributed_tokens": 100,
            "unattributed_calls": 3,
            "coverage": {"window_calls": 100, "labelled_calls": 80, "labelled_ratio": 0.8},
        }
    )
    answer = await _list(fake, "spend_agent")
    rows = answer.split("\n")
    assert rows[1] == "agent | calls | invocations | tokens | list $"
    assert rows[2] == "reviewer | 30 | 5 | 9K | $4.50"
    assert "unattributed: 100 tokens, 3 calls" in answer
    assert "80% of 100 calls" in answer


async def test_an_unavailable_agent_answer_gives_the_backends_reason() -> None:
    fake = FakeSpend(agents={"available": False, "unavailable_reason": "no agent columns here"})
    answer = await _list(fake, "spend_agent")
    assert "unavailable: no agent columns here" in answer


# --- narrowing to one person ----------------------------------------------- #


@pytest.mark.parametrize("entity_type", ["spend_summary", "spend_lane", "spend_agent"])
async def test_a_user_email_filter_reaches_the_body(entity_type: str) -> None:
    fake = FakeSpend()
    await _list(fake, entity_type, filters='user_email = "dev@example.com"')
    assert fake.last["user_email"] == "dev@example.com"


async def test_the_leaderboard_takes_a_user_email_filter_too() -> None:
    fake = FakeSpend()
    await _list(fake, "spend_user", filters='user_email = "dev@example.com"')
    assert fake.last["user_email"] == "dev@example.com"


async def test_a_user_email_filter_only_accepts_equals() -> None:
    with pytest.raises(ToolError, match="not valid for 'user_email'"):
        await _list(FakeSpend(), "spend_summary", filters='user_email contains "dev"')


# --- empty, sizes, errors -------------------------------------------------- #


@pytest.mark.parametrize("entity_type", SPEND_TYPES)
async def test_an_empty_window_says_there_is_no_usage_and_is_not_an_error(
    entity_type: str,
) -> None:
    fake = FakeSpend(
        summary={"results": [{"name": "total_messages", "current": 0}]},
        composition={"input": {"total_tokens": 0}, "output": {"total_tokens": 0}},
    )
    answer = await _list(fake, entity_type)
    assert "No Claude Code usage in " in answer
    assert "admin" not in answer


@pytest.mark.parametrize("entity_type", SPEND_TYPES)
async def test_every_list_states_its_size(entity_type: str) -> None:
    answer = await _list(FakeSpend(), entity_type)
    assert re.match(rf"\[list: {entity_type} \| [\d,]+ tok \| ", _first_line(answer))


async def test_the_admin_error_is_the_errors_own_sentence_on_list_and_read() -> None:
    fake = FakeSpend(error=SpendAdminRequiredError(ADMIN_SENTENCE))
    with pytest.raises(ToolError) as listed:
        await _list(fake, "spend_user")
    with pytest.raises(ToolError) as read:
        await _read(fake, "spend_lane", "skills")
    assert str(listed.value) == ADMIN_SENTENCE
    assert str(read.value) == ADMIN_SENTENCE


@pytest.mark.parametrize(
    ("call", "named"),
    [
        (partial(_list, entity_type="spend_summary", sort="x"), "sort"),
        (partial(_list, entity_type="spend_summary", page=2), "page"),
        (partial(_list, entity_type="spend_lane", size=5), "size"),
        (partial(_list, entity_type="spend_agent", fields=["a"]), "fields"),
        (partial(_list, entity_type="spend_user", search="x"), "search"),
        (partial(_list, entity_type="spend_session", name="x"), "name"),
        (partial(_list, entity_type="spend_user", project_name="other"), "project_name"),
    ],
)
async def test_arguments_a_type_does_not_honor_are_refused_in_one_line(
    call: Callable[[FakeSpend], Awaitable[str]], named: str
) -> None:
    fake = FakeSpend()
    with pytest.raises(ToolError) as exc:
        await call(fake)
    assert f"does not take {named}" in str(exc.value)
    assert "\n" not in str(exc.value)
    assert fake.calls == []


def test_schema_answers_for_the_spend_types_with_their_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("opik_mcp.writes.schema_tool.get_settings", lambda: SPEND)
    reference = run_schema("list.spend_session")
    assert set(reference["filters"]["fields"]) == {
        "user_email",
        "turns",
        "total_tokens",
        "duration",
        "start_time",
    }
    assert "last_activity" in reference["sort"]["fields"]


# --- a default workspace ------------------------------------------------------ #


class _NoBackend:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"backend touched: {name}")


@pytest.mark.parametrize("entity_type", SPEND_TYPES)
async def test_a_default_workspace_refuses_every_spend_type_as_unknown(entity_type: str) -> None:
    client = cast("OpikListClient", _NoBackend())
    with pytest.raises(ToolError, match="Cannot list") as listed:
        await run_list(entity_type, settings=DEFAULT, client=client)
    with pytest.raises(ToolError, match="Invalid entity_type") as read:
        await run_read(
            entity_type, "x", settings=DEFAULT, client=cast("OpikReadClient", _NoBackend())
        )
    for refusal in (listed, read):
        assert "AI Spend" not in str(refusal.value)
        assert "spend_" not in str(refusal.value).replace(repr(entity_type), "")


def test_the_default_surface_names_no_spend_type() -> None:
    advertised = (
        *registry.READABLE_TYPES,
        *registry.LISTABLE_TYPES,
        *registry.SORTABLE_TYPES,
        *registry.FILTERABLE_TYPES,
        *registry.WINDOWED_TYPES,
        *LIST_SCHEMA_KEYS,
    )
    assert not [name for name in advertised if "spend" in name], (
        "a spend type leaked into the default types: declare "
        "feature=AI_SPEND_FEATURE on its handler in entities/spend."
    )


def test_every_spend_handler_is_behind_the_feature() -> None:
    handlers = [registry.ENTITY_REGISTRY[name] for name in SPEND_TYPES]
    assert all(h.feature == AI_SPEND_FEATURE for h in handlers)


def test_the_spend_workspace_adds_exactly_the_spend_types() -> None:
    features = enabled_features(SPEND)
    assert added_readable(features) == ["spend_lane", "spend_session"]
    assert sorted(added_listable(features)) == sorted(SPEND_TYPES)
    assert added_schema_keys(features) == sorted(f"list.{name}" for name in SPEND_TYPES)
    assert set(SPEND_TYPES) <= set(listable_types(features))
    assert {"spend_lane", "spend_session"} <= set(readable_types(features))


async def test_a_missing_dollar_field_reads_n_a_not_zero() -> None:
    fake = FakeSpend(
        summary={"results": [{"name": "m", "current": 1.0}], "spend_api_usd": 5.0},
        users={"total": 1, "content": [{"user_email": "a@example.com", "total_tokens": 10}]},
    )
    summary = await _list(fake, "spend_summary")
    assert "billed $5.00 = seat n/a + over-plan n/a + API $5.00" in summary
    assert "list value at API rates n/a" in summary
    row = (await _list(fake, "spend_user")).split("\n")[2]
    assert row.split(" | ")[3:7] == ["n/a", "n/a", "n/a", "n/a"]


async def test_a_zero_dollar_lane_ranks_above_a_lane_with_no_dollars() -> None:
    fake = FakeSpend(
        composition={
            "input": {
                "total_tokens": 901,
                "cost_usd": 1.0,
                "lanes": [
                    {"key": "memory", "label": "Memory", "total_tokens": 900},
                    {"key": "skills", "label": "Skills", "total_tokens": 1, "cost_usd": 0.0},
                ],
            },
            "output": {"lanes": []},
        }
    )
    rows = (await _list(fake, "spend_lane")).split("\n")
    assert rows[2].startswith("input | skills")
    assert rows[3].startswith("input | memory")


async def test_a_missing_total_falls_back_to_the_row_count() -> None:
    fake = FakeSpend(
        users={"content": [_user_row("a@example.com", 5)]},
        sessions={"content": [_session_row(SESSION)]},
    )
    assert "page 1/1 | 1 users" in _first_line(await _list(fake, "spend_user"))
    assert "page 1/1 | 1 sessions" in _first_line(await _list(fake, "spend_session"))


async def test_a_lane_read_counts_the_hidden_items_from_what_the_backend_returned() -> None:
    items = [{"label": f"item-{i}", "total_tokens": i, "cost_usd": float(i)} for i in range(30)]
    fake = FakeSpend(breakdown={"title": "MCP servers", "item_count": 4_000, "items": items})
    data = json.loads((await _read(fake, "spend_lane", "mcp_servers")).split("\n", 1)[1])
    assert len(data["items"]) == TOP_ITEMS
    assert data["more"].startswith("5 more items")


async def test_the_guide_says_the_outline_filter_leaves_some_background_turns() -> None:
    guide = run_read_skill(GUIDE_NAME, enabled_features(SPEND))
    step = guide[guide.index("If the narrative isn't ready") :]
    step = step[: step.index("**Which subagents")]
    assert 'name not_contains "automated"' in step
    for remaining in ("Status-line prompts", "recaps", "cross-session messages"):
        assert remaining in step, f"the outline step no longer names {remaining!r}"
    assert "without automated" not in guide


async def test_session_rows_link_to_their_analysis_page_and_the_column_says_turns() -> None:
    fake = FakeSpend(sessions={"total": 1, "content": [_session_row(SESSION)]})
    answer = await _list(fake, "spend_session")
    assert "| turns |" in answer.split("\n")[1]
    (link,) = [line for line in answer.split("\n") if line.startswith("Open a row in Opik")]
    assert "/__ai_spend_test__/ai-spend/session-analysis/{id}" in link
    assert "Open in Opik" not in answer


@pytest.mark.parametrize("filters", [None, 'mcp_server = "github"'])
async def test_user_rows_link_to_the_leaderboard_not_home(filters: str | None) -> None:
    fake = FakeSpend(
        users={"total": 1, "content": [_user_row("a@example.com", 5)]},
        item_users=[{"user_email": "a@example.com", "calls": 1}],
    )
    answer = await _list(fake, "spend_user", filters=filters)
    (link,) = [line for line in answer.split("\n") if line.startswith("Open in Opik")]
    assert "/__ai_spend_test__/ai-spend/leaderboard" in link
    assert "Open a row" not in answer
    assert "/ai-spend/home" not in answer

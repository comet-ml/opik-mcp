"""Unit tests for the ``read`` tool — dispatch, name-lookup, errors.

Uses a duck-typed ``FakeOpikClient`` so we can exercise the registry's
fetcher functions without spinning up httpx mocks.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikNotFoundError, OpikServerError, OpikValidationError
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.read_tool import run_read


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeOpikClient:
    """Stand-in for ``OpikClient`` — only implements the read endpoints."""

    projects_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    projects_by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    traces_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    spans_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace_spans: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    experiments_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    experiments_by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    test_suites_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompts_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompt_versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    threads_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    thread_messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    issues_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_issue_kwargs: dict[str, Any] = field(default_factory=dict)
    fail_issue_with: Exception | None = None
    project_lookups: int = 0
    fail_list_traces: bool = False
    kpi_stats: list[dict[str, Any]] = field(default_factory=list)
    last_kpi_kwargs: dict[str, Any] = field(default_factory=dict)
    fail_kpi_with: Exception | None = None
    score_names: dict[str, Any] = field(default_factory=lambda: {"scores": []})
    usage_keys: dict[str, Any] = field(default_factory=lambda: {"names": []})
    automation_rules: dict[str, Any] = field(
        default_factory=lambda: {"content": [], "page": 1, "size": 0, "total": 0}
    )
    fail_score_names_with: Exception | None = None
    in_flight: int = 0
    max_in_flight: int = 0

    async def _concurrently(self, result: dict[str, Any]) -> dict[str, Any]:
        """Yield once so overlapping callers are observable.

        A serial fan-out never gets two of these in flight at the same time;
        a gathered one gets all of them. That difference is the only way to
        tell the two apart from outside.
        """
        self.in_flight += 1
        try:
            await asyncio.sleep(0)
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            return result
        finally:
            self.in_flight -= 1

    async def get_project(self, project_id: str) -> dict[str, Any]:
        if project_id not in self.projects_by_id:
            raise OpikNotFoundError(f"project {project_id!r} not found (404).")
        return self.projects_by_id[project_id]

    async def get_project_kpi_cards(
        self,
        project_id: str,
        /,
        *,
        entity_type: str,
        interval_start: str,
        interval_end: str | None = None,
        filters: str | None = None,
    ) -> dict[str, Any]:
        self.last_kpi_kwargs = {
            "project_id": project_id,
            "entity_type": entity_type,
            "interval_start": interval_start,
            "interval_end": interval_end,
            "filters": filters,
        }
        if self.fail_kpi_with is not None:
            raise self.fail_kpi_with
        return await self._concurrently({"stats": self.kpi_stats})

    async def list_projects(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        self.project_lookups += 1
        content = self.projects_by_name.get(name or "", [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        if trace_id not in self.traces_by_id:
            raise OpikNotFoundError(f"trace {trace_id!r} not found (404).")
        return self.traces_by_id[trace_id]

    async def list_spans(
        self,
        *,
        trace_id: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 100,
        **_search: Any,
    ) -> dict[str, Any]:
        content = self.trace_spans.get(trace_id or "", [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def get_span(self, span_id: str) -> dict[str, Any]:
        return self.spans_by_id[span_id]

    async def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        return self.experiments_by_id[experiment_id]

    async def list_experiments(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
        **_search: Any,
    ) -> dict[str, Any]:
        content = self.experiments_by_name.get(name or "", [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def get_test_suite(self, test_suite_id: str) -> dict[str, Any]:
        return self.test_suites_by_id[test_suite_id]

    async def list_test_suites(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def get_prompt(self, prompt_id: str) -> dict[str, Any]:
        return self.prompts_by_id[prompt_id]

    async def list_prompt_versions(
        self,
        prompt_id: str,
        *,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        content = self.prompt_versions.get(prompt_id, [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def list_prompts(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def get_thread(
        self,
        thread_id: str,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        truncate: bool = False,
    ) -> dict[str, Any]:
        if thread_id not in self.threads_by_id:
            raise OpikNotFoundError(f"thread {thread_id!r} not found (404).")
        return self.threads_by_id[thread_id]

    async def list_traces(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        page: int = 1,
        size: int = 10,
        **_search: Any,
    ) -> dict[str, Any]:
        if self.fail_list_traces:
            raise OpikServerError("boom")
        # Honor a thread_id filter so the thread fetcher's messages call works.
        if filters:
            for f in json.loads(filters):
                if f.get("field") == "thread_id":
                    content = self.thread_messages.get(f.get("value"), [])
                    return {
                        "content": content,
                        "page": page,
                        "size": len(content),
                        "total": len(content),
                    }
        return {"content": [], "page": page, "size": 0, "total": 0}

    # OpikListClient surface the read tool doesn't exercise — present so the
    # fake satisfies the Protocol structurally.
    async def list_threads(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def list_test_suite_items(self, _test_suite_id: str, **_kw: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def list_agent_insights_issues(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def list_project_score_names(self, _project_id: str, /) -> dict[str, Any]:
        if self.fail_score_names_with is not None:
            raise self.fail_score_names_with
        return await self._concurrently(self.score_names)

    async def list_project_token_usage_names(self, _project_id: str, /) -> dict[str, Any]:
        return await self._concurrently(self.usage_keys)

    async def list_automation_rules(self, **_: Any) -> dict[str, Any]:
        return await self._concurrently(self.automation_rules)

    async def get_agent_insights_issue(
        self,
        issue_id: str,
        *,
        project_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        self.last_issue_kwargs = {
            "project_id": project_id,
            "from_date": from_date,
            "to_date": to_date,
        }
        if self.fail_issue_with is not None:
            raise self.fail_issue_with
        if issue_id not in self.issues_by_id:
            raise OpikNotFoundError(f"agent insights issue {issue_id!r} not found (404).")
        return self.issues_by_id[issue_id]


UUID = "11111111-2222-3333-4444-555555555555"


# --- happy paths ---------------------------------------------------------- #


@pytest.mark.anyio
async def test_read_project_by_uuid_returns_header_and_json() -> None:
    fake = FakeOpikClient(projects_by_id={UUID: {"id": UUID, "name": "demo"}})
    out = await run_read("project", UUID, client=fake)

    assert out.startswith(f"[read: project {UUID}")
    assert "compression=FULL" in out
    assert UUID in out
    assert "demo" in out


@pytest.mark.anyio
async def test_read_trace_inlines_spans() -> None:
    fake = FakeOpikClient(
        traces_by_id={UUID: {"id": UUID, "name": "t", "project_id": "p-1"}},
        trace_spans={UUID: [{"id": "sp-1", "name": "child"}]},
    )
    out = await run_read("trace", UUID, client=fake)
    assert '"spans"' in out
    assert "sp-1" in out
    assert '"spansTruncated"' in out
    assert "false" in out


@pytest.mark.anyio
async def test_read_trace_without_project_id_returns_empty_spans() -> None:
    fake = FakeOpikClient(traces_by_id={UUID: {"id": UUID, "name": "t"}})
    out = await run_read("trace", UUID, client=fake)
    assert '"spans": []' in out


@pytest.mark.anyio
async def test_read_prompt_inlines_versions() -> None:
    fake = FakeOpikClient(
        prompts_by_id={UUID: {"id": UUID, "name": "p"}},
        prompt_versions={UUID: [{"id": "v-1"}]},
    )
    out = await run_read("prompt", UUID, client=fake)
    assert '"versions"' in out
    assert "v-1" in out
    assert '"versionsTruncated"' in out


# --- name lookup ---------------------------------------------------------- #


@pytest.mark.anyio
async def test_read_project_by_name_resolves_to_unique_match() -> None:
    """Name → id resolution: fetch hits the resolved record. The header echoes
    the original input (so the user sees what they asked for); the body
    carries the resolved record."""
    fake = FakeOpikClient(
        projects_by_id={UUID: {"id": UUID, "name": "demo"}},
        projects_by_name={"demo": [{"id": UUID, "name": "demo"}]},
    )
    out = await run_read("project", "demo", client=fake)
    assert UUID in out
    assert "demo" in out


@pytest.mark.anyio
async def test_read_project_by_ambiguous_name_lists_candidates() -> None:
    fake = FakeOpikClient(
        projects_by_name={
            "demo": [
                {"id": "p-1", "name": "demo"},
                {"id": "p-2", "name": "demo-2"},
            ]
        },
    )
    with pytest.raises(ToolError) as exc:
        await run_read("project", "demo", client=fake)
    assert "Multiple projects match" in str(exc.value)
    assert "p-1" in str(exc.value)
    assert "p-2" in str(exc.value)
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_trace_skips_name_lookup_id_only_entity() -> None:
    """Traces are id-only — non-UUID input goes straight to fetch, which 404s."""
    fake = FakeOpikClient()
    with pytest.raises(ToolError, match="Not found"):
        await run_read("trace", "not-a-uuid", client=fake)


# --- URI input ------------------------------------------------------------ #


@pytest.mark.anyio
async def test_read_accepts_opik_uri_overriding_entity_type() -> None:
    """Passing a URI to ``id`` flips ``entity_type`` to match the URI."""
    fake = FakeOpikClient(projects_by_id={UUID: {"id": UUID, "name": "demo"}})
    out = await run_read("trace", f"opik://projects/{UUID}", client=fake)
    assert f"[read: project {UUID}" in out


@pytest.mark.anyio
async def test_read_rejects_malformed_uri() -> None:
    with pytest.raises(ToolError, match="opik://"):
        await run_read("trace", "opik://nonsense/x", client=FakeOpikClient())


# --- threads -------------------------------------------------------------- #

THREAD = "conv-1"
_THREAD_META = {"id": THREAD, "status": "active", "project_id": "p-9"}


def _thread_fake() -> FakeOpikClient:
    return FakeOpikClient(
        threads_by_id={THREAD: _THREAD_META},
        thread_messages={
            THREAD: [
                {"id": "tr-2", "name": "turn2", "input": "b", "start_time": "2026-01-02"},
                {"id": "tr-1", "name": "turn1", "input": "a", "start_time": "2026-01-01"},
            ]
        },
    )


@pytest.mark.anyio
async def test_read_thread_assembles_sorted_messages() -> None:
    out = await run_read("thread", THREAD, project_id="p-9", client=_thread_fake())
    assert f"[read: thread {THREAD}" in out
    assert '"messages"' in out
    assert '"messagesTruncated": false' in out
    # ascending by start_time → tr-1 (2026-01-01) precedes tr-2 (2026-01-02)
    assert out.index("tr-1") < out.index("tr-2")
    assert '"trace_id"' in out


@pytest.mark.anyio
async def test_read_thread_via_project_name() -> None:
    out = await run_read("thread", THREAD, project_name="demo", client=_thread_fake())
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_requires_project() -> None:
    with pytest.raises(ToolError) as exc:
        await run_read("thread", THREAD, client=_thread_fake())
    assert "requires project scope" in str(exc.value)
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_thread_via_canonical_uri_carries_project() -> None:
    # URI supplies the project, so no explicit project arg is needed.
    out = await run_read("thread", "opik://projects/p-9/threads/conv-1", client=_thread_fake())
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_via_web_url() -> None:
    url = "https://app.opik.test/ws/projects/p-9/traces?thread=conv-1"
    out = await run_read("thread", url, client=_thread_fake())
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_degrades_on_messages_failure() -> None:
    fake = _thread_fake()
    fake.fail_list_traces = True
    out = await run_read("thread", THREAD, project_id="p-9", client=fake)
    assert '"messages": []' in out
    assert '"messagesTruncated": false' in out
    # the load failure is surfaced, not silently reported as "no messages"
    assert "messagesError" in out
    # metadata still present
    assert '"status"' in out


@pytest.mark.anyio
async def test_read_thread_not_found() -> None:
    fake = FakeOpikClient()
    with pytest.raises(ToolError, match="Not found"):
        await run_read("thread", THREAD, project_id="p-9", client=fake)


# --- validation / errors -------------------------------------------------- #


@pytest.mark.anyio
async def test_read_rejects_unknown_entity_type() -> None:
    with pytest.raises(ToolError, match="Invalid entity_type"):
        await run_read("widget", UUID, client=FakeOpikClient())


@pytest.mark.anyio
async def test_read_rejects_list_only_entity() -> None:
    with pytest.raises(ToolError, match="list-only"):
        await run_read("test_suite_item", UUID, client=FakeOpikClient())


@pytest.mark.anyio
async def test_read_surfaces_not_found_with_hint() -> None:
    fake = FakeOpikClient()
    with pytest.raises(ToolError) as exc:
        await run_read("project", UUID, client=fake)
    msg = str(exc.value)
    assert "Not found" in msg
    assert UUID in msg


# --- agent_insights_issue (Diagnostics) ---------------------------------- #

ISSUE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_ISSUE_DETAIL = {
    "id": ISSUE,
    "name": "Tool call loop on weather lookup",
    "description": "The agent called get_weather 12 times in one turn.",
    "cause": "Retries on API timeout have no cap.",
    "suggested_fix": "Cap retries at 2 and surface the timeout.",
    "status": "open",
    "severity": "high",
    "traces_query": "tool_name = 'get_weather'",
    "details": [
        {
            "report_day": "2026-09-06",
            "count": 7,
            "total_count": 120,
            "users_impacted": 3,
            "total_users": 40,
            "metadata": {"example_trace_ids": ["tr-a", "tr-b"], "confidence": 0.9},
        },
        {
            "report_day": "2026-09-07",
            "count": 5,
            "total_count": 110,
            "users_impacted": 2,
            "total_users": 38,
            "metadata": {"example_trace_ids": ["tr-b", "tr-c"]},
        },
    ],
}


def _issue_fake(detail: dict[str, Any] | None = None) -> FakeOpikClient:
    return FakeOpikClient(issues_by_id={ISSUE: detail if detail is not None else _ISSUE_DETAIL})


def _payload(out: str) -> dict[str, Any]:
    """Strip the ``[read: …]`` header line and parse the JSON body."""
    body = json.loads(out.split("\n", 1)[1])
    assert isinstance(body, dict)
    return body


@pytest.mark.anyio
async def test_read_issue_returns_issue_example_trace_ids_and_details() -> None:
    out = await run_read("agent_insights_issue", ISSUE, project_id="p-9", client=_issue_fake())
    assert f"[read: agent_insights_issue {ISSUE}" in out
    body = _payload(out)
    assert {"issue", "example_trace_ids", "details"} <= set(body)
    # The project the issue was read under feeds the UI links and never leaks.
    assert "_project_id" not in body
    # The issue record is the backend's, minus the per-day rows.
    assert body["issue"]["name"] == "Tool call loop on weather lookup"
    assert body["issue"]["suggested_fix"] == "Cap retries at 2 and surface the timeout."
    assert "details" not in body["issue"]
    # Deduped across days, first-seen order over ascending report days.
    assert body["example_trace_ids"] == ["tr-a", "tr-b", "tr-c"]
    # Per-day rows pass through unchanged.
    assert body["details"] == _ISSUE_DETAIL["details"]


@pytest.mark.anyio
async def test_read_issue_passes_project_and_window_to_client() -> None:
    fake = _issue_fake()
    await run_read(
        "agent_insights_issue",
        ISSUE,
        project_id="p-9",
        since="2026-09-01T15:30:00Z",
        until="2026-09-08T02:00:00+02:00",
        client=fake,
    )
    assert fake.last_issue_kwargs == {
        "project_id": "p-9",
        "from_date": "2026-09-01",
        "to_date": "2026-09-08",
    }


@pytest.mark.anyio
async def test_read_issue_accepts_relative_window() -> None:
    fake = _issue_fake()
    await run_read("agent_insights_issue", ISSUE, project_id="p-9", since="7d", client=fake)
    from_date = fake.last_issue_kwargs["from_date"]
    assert isinstance(from_date, str) and len(from_date) == 10
    assert fake.last_issue_kwargs["to_date"] is None


@pytest.mark.anyio
async def test_read_issue_inverted_window_rejected_before_backend() -> None:
    fake = _issue_fake()
    with pytest.raises(ToolError, match="before since"):
        await run_read(
            "agent_insights_issue",
            ISSUE,
            project_id="p-9",
            since="2026-09-09T00:00:00Z",
            until="2026-09-01T00:00:00Z",
            client=fake,
        )
    assert fake.last_issue_kwargs == {}


@pytest.mark.anyio
async def test_read_issue_window_omitted_by_default() -> None:
    fake = _issue_fake()
    await run_read("agent_insights_issue", ISSUE, project_id="p-9", client=fake)
    assert fake.last_issue_kwargs["from_date"] is None
    assert fake.last_issue_kwargs["to_date"] is None


@pytest.mark.anyio
async def test_read_issue_tolerates_rows_without_example_ids() -> None:
    detail = {
        **_ISSUE_DETAIL,
        "details": [
            {"report_day": "2026-09-01", "count": 1},  # no metadata at all
            {"report_day": "2026-09-02", "count": 1, "metadata": "corrupt"},  # not an object
            {"report_day": "2026-09-03", "count": 1, "metadata": {"confidence": 0.5}},
            {"report_day": "2026-09-04", "count": 1, "metadata": {"example_trace_ids": ["tr-z"]}},
        ],
    }
    out = await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", client=_issue_fake(detail)
    )
    assert _payload(out)["example_trace_ids"] == ["tr-z"]


@pytest.mark.anyio
async def test_read_issue_with_no_details_has_empty_example_ids() -> None:
    detail = {**_ISSUE_DETAIL, "details": []}
    out = await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", client=_issue_fake(detail)
    )
    body = _payload(out)
    assert body["example_trace_ids"] == []
    assert body["details"] == []


@pytest.mark.anyio
async def test_read_issue_requires_project_scope() -> None:
    with pytest.raises(ToolError) as exc:
        await run_read("agent_insights_issue", ISSUE, client=_issue_fake())
    msg = str(exc.value)
    assert "read('agent_insights_issue') requires project scope" in msg
    assert "project_name" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_issue_not_found_names_entity_and_id() -> None:
    with pytest.raises(ToolError) as exc:
        await run_read("agent_insights_issue", "is-missing", project_id="p-9", client=_issue_fake())
    msg = str(exc.value)
    assert "agent_insights_issue" in msg
    assert "is-missing" in msg
    assert "not found" in msg


@pytest.mark.anyio
async def test_read_issue_skeleton_keeps_every_example_trace_id() -> None:
    """An all-time read can carry hundreds of per-day rows. When it blows the
    budget, the trace ids — the reason for the read — must survive; the
    per-day rows are what gets dropped."""
    rows = [
        {
            "report_day": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "count": i,
            "metadata": {
                "example_trace_ids": [f"tr-{i}"],
                "confidence_justification": "x" * 2_000,
            },
        }
        for i in range(120)
    ]
    detail = {**_ISSUE_DETAIL, "details": rows}
    out = await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", max_tokens=500, client=_issue_fake(detail)
    )
    assert "compression=SKELETON" in out
    body = _payload(out)
    assert body["issue"]["id"] == ISSUE
    assert body["issue"]["name"] == "Tool call loop on weather lookup"
    assert body["issue"]["severity"] == "high"
    assert body["issue"]["status"] == "open"
    assert body["example_trace_ids"] == [f"tr-{i}" for i in range(120)]
    assert "details" not in body
    assert "read('trace'" in body["note"]


@pytest.mark.anyio
async def test_read_issue_resolves_exact_project_name() -> None:
    fake = _issue_fake()
    fake.projects_by_name = {
        "demo": [{"id": "p-demo-2", "name": "demo-2"}, {"id": "p-demo", "name": "demo"}]
    }
    out = await run_read("agent_insights_issue", ISSUE, project_name="demo", client=fake)
    assert f"[read: agent_insights_issue {ISSUE}" in out
    assert fake.last_issue_kwargs["project_id"] == "p-demo"


@pytest.mark.anyio
async def test_read_issue_resolves_project_name_case_insensitively() -> None:
    fake = _issue_fake()
    fake.projects_by_name = {"Support-Agent-Demo": [{"id": "p-demo", "name": "support-agent-demo"}]}
    out = await run_read(
        "agent_insights_issue", ISSUE, project_name="Support-Agent-Demo", client=fake
    )
    assert f"[read: agent_insights_issue {ISSUE}" in out
    assert fake.last_issue_kwargs["project_id"] == "p-demo"


@pytest.mark.anyio
async def test_read_issue_ambiguous_project_name_lists_candidates() -> None:
    fake = _issue_fake()
    fake.projects_by_name = {"demo": [{"id": "p-1", "name": "demo"}, {"id": "p-2", "name": "demo"}]}
    with pytest.raises(ToolError) as exc:
        await run_read("agent_insights_issue", ISSUE, project_name="demo", client=fake)
    msg = str(exc.value)
    assert "p-1" in msg and "p-2" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_issue_unknown_project_name_suggests_the_closest_one() -> None:
    fake = _issue_fake()
    # The unfiltered lookup (key "") is what the suggestion is built from.
    fake.projects_by_name = {"": [{"id": "p-1", "name": "ghost-demo"}]}
    with pytest.raises(ToolError) as exc:
        await run_read("agent_insights_issue", ISSUE, project_name="gost-demo", client=fake)
    msg = str(exc.value)
    assert "Project 'gost-demo' not found." in msg
    assert "Did you mean 'ghost-demo'?" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_issue_reuses_project_id_resolved_by_an_earlier_call() -> None:
    """list then read by the same project_name is the agent's normal flow;
    the second call must not pay the projects lookup again."""
    fake = _issue_fake()
    fake.projects_by_name = {"demo": [{"id": "p-demo", "name": "demo"}]}
    await run_read("agent_insights_issue", ISSUE, project_name="demo", client=fake)
    await run_read("agent_insights_issue", ISSUE, project_name="demo", client=fake)
    assert fake.project_lookups == 1
    assert fake.last_issue_kwargs["project_id"] == "p-demo"


@pytest.mark.anyio
async def test_read_issue_project_id_wins_over_name_without_lookup() -> None:
    fake = _issue_fake()
    await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", project_name="demo", client=fake
    )
    assert fake.last_issue_kwargs["project_id"] == "p-9"
    assert fake.project_lookups == 0


@pytest.mark.anyio
async def test_read_issue_bad_window_surfaces_backend_validation_message() -> None:
    """The backend rejects from_date > to_date with a 400; the agent must see
    that reason, not a generic failure."""
    fake = _issue_fake()
    fake.fail_issue_with = OpikValidationError(
        "Opik rejected the request body (400) for agent insights issue — "
        "Parameter 'from_date' must not be after 'to_date'"
    )
    with pytest.raises(ToolError) as exc:
        await run_read(
            "agent_insights_issue",
            ISSUE,
            project_id="p-9",
            since="2026-09-01T00:00:00Z",
            until="2026-09-08T00:00:00Z",
            client=fake,
        )
    assert "from_date" in str(exc.value)
    assert isinstance(exc.value.__cause__, OpikValidationError)


@pytest.mark.anyio
async def test_read_issue_via_pasted_diagnostics_link() -> None:
    """The link carries the project, so no scope argument is needed, and it
    overrides whatever entity_type the agent guessed."""
    fake = _issue_fake()
    url = f"https://www.comet.com/opik/ws/projects/p-link/diagnostics?issue={ISSUE}"
    out = await run_read("trace", url, client=fake)
    assert f"[read: agent_insights_issue {ISSUE}" in out
    assert fake.last_issue_kwargs["project_id"] == "p-link"
    assert fake.project_lookups == 0


@pytest.mark.anyio
async def test_read_issue_link_project_overrides_explicit_name() -> None:
    fake = _issue_fake()
    url = f"https://www.comet.com/opik/ws/projects/p-link/diagnostics?issue={ISSUE}"
    await run_read("agent_insights_issue", url, project_name="ignored", client=fake)
    assert fake.last_issue_kwargs["project_id"] == "p-link"
    assert fake.project_lookups == 0


@pytest.mark.anyio
async def test_read_issue_via_canonical_uri() -> None:
    fake = _issue_fake()
    out = await run_read(
        "agent_insights_issue", f"opik://projects/p-uri/agent-insights-issues/{ISSUE}", client=fake
    )
    assert f"[read: agent_insights_issue {ISSUE}" in out
    assert fake.last_issue_kwargs["project_id"] == "p-uri"


@pytest.mark.anyio
async def test_read_issue_medium_drops_row_metadata_to_meet_budget() -> None:
    """The per-row metadata (ids already lifted into example_trace_ids, plus
    prose) is the bulk of an issue read. When the FULL body is over budget,
    MEDIUM drops it first — and that alone should bring the seven-row fixture
    under a 1,000-token budget while keeping every row's counts."""
    rows = [
        {
            "report_day": f"2026-09-0{d}",
            "count": d,
            "total_count": 10 * d,
            "users_impacted": 1,
            "total_users": 40,
            "metadata": {
                "example_trace_ids": [f"tr-{d}-{i}" for i in range(5)],
                "confidence_justification": "why " * 150,
            },
        }
        for d in range(1, 8)
    ]
    detail = {**_ISSUE_DETAIL, "details": rows}
    out = await run_read(
        "agent_insights_issue",
        ISSUE,
        project_id="p-9",
        max_tokens=1_000,
        client=_issue_fake(detail),
    )
    header, payload = out.split("\n", 1)
    assert "compression=MEDIUM" in header
    assert len(payload) // 4 <= 1_000
    body = json.loads(payload)
    assert len(body["example_trace_ids"]) == 35
    assert len(body["details"]) == 7
    assert body["details"][0]["count"] == 1
    assert "metadata" not in body["details"][0]
    # The prose the agent came for is intact at this tier.
    assert body["issue"]["suggested_fix"] == _ISSUE_DETAIL["suggested_fix"]


@pytest.mark.anyio
async def test_read_issue_falls_to_skeleton_when_still_over_budget() -> None:
    """A budget too small for even the pruned rows gets SKELETON regardless of
    the global 50k threshold — the agent asked for a small answer."""
    out = await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", max_tokens=150, client=_issue_fake()
    )
    header, payload = out.split("\n", 1)
    assert "compression=SKELETON" in header
    body = json.loads(payload)
    assert body["example_trace_ids"] == ["tr-a", "tr-b", "tr-c"]
    assert body["issue"]["name"] == _ISSUE_DETAIL["name"]
    assert "details" not in body


# --- UI links on the issue read ------------------------------------------ #

_UI_SETTINGS = Settings(
    opik_api_key="k", comet_workspace="demo-ws", opik_url="https://opik.test/api"
)


@pytest.mark.anyio
async def test_read_issue_carries_diagnostics_page_url_and_trace_url_template() -> None:
    """The diagnose skill must hand back clickable links; the server knows
    the UI base and workspace, so the read supplies them instead of making
    the agent guess the URL shape."""
    out = await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", client=_issue_fake(), settings=_UI_SETTINGS
    )
    body = _payload(out)
    assert body["url"] == f"https://opik.test/demo-ws/projects/p-9/diagnostics?issue={ISSUE}"
    assert (
        body["trace_url_template"] == "https://opik.test/demo-ws/projects/p-9/logs?trace={trace_id}"
    )


@pytest.mark.anyio
async def test_read_issue_resolved_status_links_to_resolved_view() -> None:
    detail = {**_ISSUE_DETAIL, "status": "resolved"}
    out = await run_read(
        "agent_insights_issue",
        ISSUE,
        project_id="p-9",
        client=_issue_fake(detail),
        settings=_UI_SETTINGS,
    )
    assert _payload(out)["url"] == (
        f"https://opik.test/demo-ws/projects/p-9/diagnostics/resolved?issue={ISSUE}"
    )


@pytest.mark.anyio
async def test_read_issue_omits_links_under_oauth_bearer_with_unknown_workspace() -> None:
    """Hosted OAuth: the workspace is token-derived server-side. If this
    process could not name it, a link built from the static fallback would
    point at the wrong workspace, so the read carries none."""
    from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX, inbound_authorization

    tok = inbound_authorization.set(f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc")
    try:
        out = await run_read(
            "agent_insights_issue",
            ISSUE,
            project_id="p-9",
            client=_issue_fake(),
            settings=_UI_SETTINGS,
        )
    finally:
        inbound_authorization.reset(tok)
    body = _payload(out)
    assert "url" not in body
    assert "trace_url_template" not in body
    assert "_project_id" not in body


@pytest.mark.anyio
async def test_read_issue_omits_links_when_opik_url_unconfigured() -> None:
    """No URL is better than a wrong one."""
    bare = Settings(
        opik_api_key="k", comet_workspace="demo-ws", opik_url=None, comet_url_override=""
    )
    out = await run_read(
        "agent_insights_issue", ISSUE, project_id="p-9", client=_issue_fake(), settings=bare
    )
    body = _payload(out)
    assert "url" not in body
    assert "trace_url_template" not in body


@pytest.mark.anyio
async def test_read_issue_skeleton_keeps_url() -> None:
    out = await run_read(
        "agent_insights_issue",
        ISSUE,
        project_id="p-9",
        max_tokens=150,
        client=_issue_fake(),
        settings=_UI_SETTINGS,
    )
    header, payload = out.split("\n", 1)
    assert "compression=SKELETON" in header
    body = json.loads(payload)
    assert body["url"].endswith(f"diagnostics?issue={ISSUE}")
    # The skeleton keeps the example ids; the template is what makes them
    # clickable, so it stays too.
    assert body["trace_url_template"].endswith("/logs?trace={trace_id}")


@pytest.mark.anyio
async def test_read_window_rejected_for_entities_that_do_not_declare_it() -> None:
    """A thread fetcher takes no window, so the read says so (as list does)
    rather than silently ignoring it — and names the entities that do take one,
    read off the registry so the message cannot go stale."""
    with pytest.raises(ToolError, match="since/until are not supported for read\\('thread'\\)"):
        await run_read(
            "thread",
            THREAD,
            project_id="p-9",
            since="7d",
            client=_thread_fake(),
        )


@pytest.mark.anyio
async def test_read_window_refusal_names_every_entity_that_takes_one() -> None:
    with pytest.raises(ToolError) as exc:
        await run_read("thread", THREAD, project_id="p-9", since="7d", client=_thread_fake())
    message = str(exc.value)
    assert "agent_insights_issue" in message
    assert "project" in message


@pytest.mark.anyio
async def test_read_undeclared_entity_kwargs_dropped() -> None:
    """Any other kwarg an entity does not declare is dropped, not forwarded."""
    out = await run_read(
        "thread", THREAD, project_id="p-9", client=_thread_fake(), not_a_real_kwarg="x"
    )
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_has_no_link_fields() -> None:
    """Links are an issue-read affordance; other composites are unchanged."""
    out = await run_read(
        "thread", THREAD, project_id="p-9", client=_thread_fake(), settings=_UI_SETTINGS
    )
    assert "url" not in _payload(out)


@pytest.mark.anyio
async def test_read_issue_medium_compression_keeps_trace_ids_too() -> None:
    """Between FULL and SKELETON the generic string truncation runs; short ids
    are untouched, so the list stays intact."""
    rows = [
        {
            "report_day": f"2026-01-{1 + i:02d}",
            "count": i,
            "metadata": {"example_trace_ids": [f"tr-{i}"], "confidence_justification": "y" * 800},
        }
        for i in range(20)
    ]
    detail = {**_ISSUE_DETAIL, "details": rows}
    out = await run_read(
        "agent_insights_issue",
        ISSUE,
        project_id="p-9",
        max_tokens=1_000,
        client=_issue_fake(detail),
    )
    assert "compression=MEDIUM" in out
    assert _payload(out)["example_trace_ids"] == [f"tr-{i}" for i in range(20)]


# --- compression budget --------------------------------------------------- #


@pytest.mark.anyio
async def test_read_respects_max_tokens() -> None:
    fake = FakeOpikClient(projects_by_id={UUID: {"id": UUID, "blob": "x" * 50_000}})
    out = await run_read("project", UUID, max_tokens=100, client=fake)
    assert "compression=MEDIUM" in out


# --- exception chain assertions ------------------------------------------- #


@pytest.mark.anyio
async def test_read_list_only_entity_chains_typed_cause() -> None:
    """``read('test_suite_item', '<uuid>')`` — list-only entity surfaced as
    ToolError chained from EntityArgValidationError so the analytics wrapper
    buckets it as validation/400 instead of unknown."""
    with pytest.raises(ToolError) as ei:
        await run_read("test_suite_item", "00000000-0000-0000-0000-000000000000")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_invalid_entity_type_chains_typed_cause() -> None:
    """``read('not_real', '<uuid>')`` — invalid entity_type chained from
    EntityArgValidationError so the bucket is validation/400, not unknown."""
    with pytest.raises(ToolError) as ei:
        await run_read("not_real", "00000000-0000-0000-0000-000000000000")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_missing_project_scope_error_is_entity_neutral() -> None:
    """The scope error names the entity and every way to supply scope —
    project_id, project_name, or a pasted link/URI — without assuming the
    entity is a thread."""
    with pytest.raises(ToolError) as exc:
        await run_read("thread", THREAD, client=_thread_fake())
    msg = str(exc.value)
    assert "read('thread')" in msg
    assert "project_id" in msg
    assert "project_name" in msg
    assert "link" in msg


# --- project overview: the week's numbers -------------------------------- #
#
# `read('project')` used to return eight metadata fields and not one number,
# so "how is my project doing" was answered by counting raw traces. These pin
# the summary block: the four figures the Logs page cards show, the window
# they cover, and the two ways the backend's answer can mislead if passed
# through as-is (metric order, and zero-vs-no-data).

_PROJECT = {
    "id": UUID,
    "name": "demo",
    "visibility": "private",
    "last_updated_trace_at": "2026-09-09T13:41:26.910Z",
}

# The backend's own order — count, avg_duration, total_cost, errors — which is
# NOT the order the UI renders the cards in.
_STATS = [
    {"type": "count", "current_value": 1204.0, "previous_value": 980.0},
    {"type": "avg_duration", "current_value": 1830.4, "previous_value": 2100.9},
    {"type": "total_cost", "current_value": 4.12, "previous_value": 3.05},
    {"type": "errors", "current_value": 2.1, "previous_value": 4.7},
]


def _project_fake(**kw: Any) -> FakeOpikClient:
    kw.setdefault("projects_by_id", {UUID: _PROJECT})
    kw.setdefault("kpi_stats", _STATS)
    return FakeOpikClient(**kw)


@pytest.mark.anyio
async def test_read_project_returns_the_four_figures_with_current_and_previous() -> None:
    """The headline: traces, error rate, latency and cost, each against the
    period before, so the agent can say what changed without a second call."""
    body = _payload(await run_read("project", UUID, client=_project_fake()))
    traces = body["summary"]["traces"]
    assert traces["count"] == {"current": 1204.0, "previous": 980.0}
    assert traces["errors"] == {"current": 2.1, "previous": 4.7}
    assert traces["avg_duration"] == {"current": 1830.4, "previous": 2100.9}
    assert traces["total_cost"] == {"current": 4.12, "previous": 3.05}
    assert body["project"]["name"] == "demo"


@pytest.mark.anyio
async def test_read_project_maps_figures_by_type_not_by_position() -> None:
    """The backend returns the stats in its own order, and it is not the UI's.
    Reading them positionally would label cost as duration — silently, and
    plausibly, which is the worst kind of wrong."""
    shuffled = [_STATS[3], _STATS[2], _STATS[0], _STATS[1]]
    body = _payload(await run_read("project", UUID, client=_project_fake(kpi_stats=shuffled)))
    traces = body["summary"]["traces"]
    assert traces["count"]["current"] == 1204.0
    assert traces["total_cost"]["current"] == 4.12


@pytest.mark.anyio
async def test_read_project_covers_the_last_seven_days_against_the_previous_seven() -> None:
    """The default window is the one the question asks about. The backend
    derives the comparison period from the window's own length, so a 7-day
    window compares against the 7 days before it."""
    fake = _project_fake()
    body = _payload(await run_read("project", UUID, client=fake))

    sent = fake.last_kpi_kwargs
    start = datetime.fromisoformat(sent["interval_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(sent["interval_end"].replace("Z", "+00:00"))
    assert (end - start) == timedelta(days=7)
    assert end <= datetime.now(UTC) + timedelta(seconds=5)

    window = body["summary"]["window"]
    assert window["days"] == 7
    assert window["since"] == sent["interval_start"]
    assert window["until"] == sent["interval_end"]


@pytest.mark.anyio
async def test_read_project_counts_only_sdk_traffic_and_says_so() -> None:
    """The Logs page cards hardcode source = "sdk", and so does list('trace').
    A project read that quietly counted experiment and playground traffic too
    would never reconcile with the number on the user's screen."""
    fake = _project_fake()
    body = _payload(await run_read("project", UUID, client=fake))

    assert json.loads(fake.last_kpi_kwargs["filters"]) == [
        {"field": "source", "operator": "=", "value": "sdk"}
    ]
    assert fake.last_kpi_kwargs["entity_type"] == "traces"
    assert body["summary"]["source"] == "sdk"


@pytest.mark.anyio
async def test_read_project_reports_a_rate_over_no_traces_as_undefined() -> None:
    """For an empty period the backend returns a zero count AND a zero error
    rate. Passed through, that reads as "0% errors — healthy", which is advice
    someone may act on. A rate and an average over no samples are undefined;
    a count and a sum are honestly zero."""
    empty = [
        {"type": "count", "current_value": 0.0, "previous_value": 0.0},
        {"type": "avg_duration", "current_value": None, "previous_value": None},
        {"type": "total_cost", "current_value": 0.0, "previous_value": 0.0},
        {"type": "errors", "current_value": 0.0, "previous_value": 0.0},
    ]
    body = _payload(await run_read("project", UUID, client=_project_fake(kpi_stats=empty)))
    traces = body["summary"]["traces"]
    assert traces["count"] == {"current": 0.0, "previous": 0.0}
    assert traces["total_cost"] == {"current": 0.0, "previous": 0.0}
    assert traces["errors"]["current"] is None
    assert traces["avg_duration"]["current"] is None
    assert "no" in body["summary"]["note"].lower()


@pytest.mark.anyio
async def test_read_project_keeps_a_real_zero_error_rate() -> None:
    """The mirror case: traces ran and none failed. That zero is a fact and
    must survive — blanking it would be as misleading as inventing it."""
    clean = [
        {"type": "count", "current_value": 300.0, "previous_value": 0.0},
        {"type": "avg_duration", "current_value": 12.0, "previous_value": None},
        {"type": "total_cost", "current_value": 0.0, "previous_value": 0.0},
        {"type": "errors", "current_value": 0.0, "previous_value": 0.0},
    ]
    body = _payload(await run_read("project", UUID, client=_project_fake(kpi_stats=clean)))
    assert body["summary"]["traces"]["errors"]["current"] == 0.0
    assert "note" not in body["summary"]


@pytest.mark.anyio
async def test_read_project_says_the_metrics_failed_rather_than_reporting_zeros() -> None:
    """A block that could not be loaded must never look like data. Same rule as
    a thread's messages: an empty answer where the metadata says otherwise is
    worse than an error."""
    fake = _project_fake(fail_kpi_with=OpikServerError("kpi-cards 503"))
    body = _payload(await run_read("project", UUID, client=fake))

    assert body["project"]["name"] == "demo", "the project record still arrives"
    assert "traces" not in body["summary"]
    assert "503" in body["summary"]["error"]
    assert "list('trace'" in body["summary"]["error"], "the error names a way forward"


@pytest.mark.anyio
async def test_read_project_fails_when_the_project_record_fails() -> None:
    """The project is the primary payload — unlike the summary, there is no
    useful answer without it."""
    with pytest.raises(ToolError, match="Not found"):
        await run_read("project", UUID, client=FakeOpikClient())


# --- project overview: the vocabulary ------------------------------------ #
#
# The map, not the content: which scores exist, which usage keys are recorded,
# what is scoring the traces. Each was a separate discovery call, and an agent
# that did not know to make them wrote filters against guessed names. Two of
# the three lists are unbounded on the backend, so each is capped by count and
# says how many there really are.


def _vocab_fake(**kw: Any) -> FakeOpikClient:
    kw.setdefault("score_names", {"scores": [{"name": "hallucination"}, {"name": "tone"}]})
    kw.setdefault("usage_keys", {"names": ["prompt_tokens", "completion_tokens"]})
    kw.setdefault(
        "automation_rules",
        {"content": [{"id": "r-1", "name": "judge"}], "total": 1},
    )
    return _project_fake(**kw)


@pytest.mark.anyio
async def test_read_project_carries_the_scores_usage_keys_and_rules() -> None:
    body = _payload(await run_read("project", UUID, client=_vocab_fake()))
    vocab = body["vocabulary"]
    assert vocab["score_names"]["names"] == ["hallucination", "tone"]
    assert vocab["usage_keys"]["names"] == ["prompt_tokens", "completion_tokens"]
    assert vocab["online_rules"]["names"] == ["judge"]


@pytest.mark.anyio
async def test_read_project_caps_a_long_score_list_and_says_how_many_there_are() -> None:
    """The backend's score-name query has no LIMIT, so a project with several
    judge rules can carry enough names to dominate the payload. The cap keeps
    the read predictable; the total keeps it honest."""
    many = {"scores": [{"name": f"score-{i:03d}"} for i in range(60)]}
    body = _payload(await run_read("project", UUID, client=_vocab_fake(score_names=many)))
    scores = body["vocabulary"]["score_names"]
    assert len(scores["names"]) == 25
    assert scores["total"] == 60
    assert "list('score_name'" in scores["all"]
    assert UUID in scores["all"], "the pointer is callable as written"


@pytest.mark.anyio
async def test_read_project_reports_a_total_even_when_nothing_was_cut() -> None:
    """A total that appeared only on truncation would make its presence mean
    "truncated", and the agent would read the short lists as complete only by
    inference. It is always there; the pointer is what signals truncation."""
    body = _payload(await run_read("project", UUID, client=_vocab_fake()))
    scores = body["vocabulary"]["score_names"]
    assert scores["total"] == 2
    assert "all" not in scores


@pytest.mark.anyio
async def test_read_project_omits_a_vocabulary_part_that_is_genuinely_empty() -> None:
    """ "Nothing recorded yet" and "could not load" have to stay
    distinguishable, so an empty part is absent rather than an empty list."""
    body = _payload(await run_read("project", UUID, client=_vocab_fake(usage_keys={"names": []})))
    assert "usage_keys" not in body["vocabulary"]
    assert "score_names" in body["vocabulary"]


@pytest.mark.anyio
async def test_read_project_omits_the_vocabulary_when_the_project_has_none() -> None:
    body = _payload(
        await run_read(
            "project",
            UUID,
            client=_vocab_fake(
                score_names={"scores": []},
                usage_keys={"names": []},
                automation_rules={"content": [], "total": 0},
            ),
        )
    )
    assert "vocabulary" not in body


@pytest.mark.anyio
async def test_read_project_reports_a_failed_vocabulary_part_without_losing_the_rest() -> None:
    fake = _vocab_fake(fail_score_names_with=OpikServerError("names 500"))
    body = _payload(await run_read("project", UUID, client=fake))
    vocab = body["vocabulary"]
    assert "500" in vocab["score_names"]["error"]
    assert "names" not in vocab["score_names"], "a failed part must not look like data"
    assert vocab["usage_keys"]["names"] == ["prompt_tokens", "completion_tokens"]
    assert body["summary"]["traces"]["count"]["current"] == 1204.0


@pytest.mark.anyio
async def test_read_project_gathers_its_calls_concurrently() -> None:
    """Five backend calls on one connection. Run in series they would cost
    five round trips; the whole point of owning the connection was to make
    them cost one."""
    fake = _vocab_fake()
    await run_read("project", UUID, client=fake)
    assert fake.max_in_flight > 1, (
        f"calls peaked at {fake.max_in_flight} in flight — the fan-out is serial"
    )


@pytest.mark.anyio
async def test_read_project_carries_a_link_to_its_page() -> None:
    body = _payload(await run_read("project", UUID, client=_project_fake(), settings=_UI_SETTINGS))
    assert body["url"] == f"https://opik.test/demo-ws/projects/{UUID}/logs"


@pytest.mark.anyio
async def test_read_project_accepts_a_relative_window() -> None:
    """`since='30d'` is what reproduces the Logs page cards, which open on 30
    days. The default answers the weekly question; this answers "and last
    month?" without a second tool."""
    fake = _project_fake()
    body = _payload(await run_read("project", UUID, since="30d", client=fake))

    sent = fake.last_kpi_kwargs
    start = datetime.fromisoformat(sent["interval_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(sent["interval_end"].replace("Z", "+00:00"))
    assert (end - start) == timedelta(days=30)
    assert body["summary"]["window"]["days"] == 30


@pytest.mark.anyio
async def test_read_project_measures_a_relative_window_from_one_clock_reading() -> None:
    """Regression, found live rather than here: the start resolved against one
    "now" and the end was measured from a second one, so `since='30d'` spanned
    30 days and a second whenever the two readings crossed a second boundary —
    and the day count silently vanished. The read closes an open-ended instant
    window itself, from the same reading, so the fetcher does no clock work."""
    fake = _project_fake()
    body = _payload(await run_read("project", UUID, since="30d", client=fake))

    sent = fake.last_kpi_kwargs
    assert sent["interval_end"] is not None, "an instant window is always closed"
    assert sent["interval_start"] == body["summary"]["window"]["since"]
    assert sent["interval_end"] == body["summary"]["window"]["until"]
    start = datetime.fromisoformat(sent["interval_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(sent["interval_end"].replace("Z", "+00:00"))
    assert (end - start) == timedelta(days=30), "no drift between the two bounds"


@pytest.mark.anyio
async def test_read_issue_window_stays_open_ended_by_default() -> None:
    """The mirror of the above: a day-keyed window must NOT be closed for the
    caller. The Diagnostics page counts all-time, so filling in "until now"
    would quietly bound what the agent asked to be unbounded."""
    fake = _issue_fake()
    await run_read("agent_insights_issue", ISSUE, project_id="p-9", since="7d", client=fake)
    assert fake.last_issue_kwargs["from_date"] is not None
    assert fake.last_issue_kwargs["to_date"] is None


@pytest.mark.anyio
async def test_read_project_accepts_absolute_instants() -> None:
    fake = _project_fake()
    body = _payload(
        await run_read(
            "project",
            UUID,
            since="2026-09-01T00:00:00Z",
            until="2026-09-08T00:00:00Z",
            client=fake,
        )
    )
    assert fake.last_kpi_kwargs["interval_start"] == "2026-09-01T00:00:00Z"
    assert fake.last_kpi_kwargs["interval_end"] == "2026-09-08T00:00:00Z"
    assert body["summary"]["window"]["since"] == "2026-09-01T00:00:00Z"
    assert body["summary"]["window"]["until"] == "2026-09-08T00:00:00Z"


@pytest.mark.anyio
async def test_read_project_says_what_previous_means() -> None:
    """ "Previous" is the backend's `[start - (end - start), start)`. Spelling it
    out beats making the agent do date arithmetic to describe its own answer —
    which is where "compared to last month" comes from when it was last week."""
    body = _payload(
        await run_read(
            "project",
            UUID,
            since="2026-09-01T00:00:00Z",
            until="2026-09-08T00:00:00Z",
            client=_project_fake(),
        )
    )
    assert body["summary"]["window"]["compared_to"] == {
        "since": "2026-08-25T00:00:00Z",
        "until": "2026-09-01T00:00:00Z",
    }


@pytest.mark.anyio
async def test_read_project_reports_a_partial_day_window_without_a_day_count() -> None:
    """A 36-hour window has no whole number of days, so claiming one would be a
    rounded lie. The bounds are always exact; `days` appears only when it is."""
    body = _payload(await run_read("project", UUID, since="36h", client=_project_fake()))
    window = body["summary"]["window"]
    assert "days" not in window
    assert window["since"] < window["until"]


@pytest.mark.anyio
async def test_read_project_rejects_an_inverted_window_before_the_backend() -> None:
    with pytest.raises(ToolError, match="before since"):
        await run_read(
            "project",
            UUID,
            since="2026-09-08T00:00:00Z",
            until="2026-09-01T00:00:00Z",
            client=_project_fake(),
        )


@pytest.mark.anyio
async def test_read_project_omits_the_link_when_opik_url_is_unconfigured() -> None:
    """No link beats a wrong one. The summary still arrives — the link is a
    convenience, the numbers are the answer."""
    bare = Settings(
        opik_api_key="k", comet_workspace="demo-ws", opik_url=None, comet_url_override=""
    )
    body = _payload(await run_read("project", UUID, client=_project_fake(), settings=bare))
    assert "url" not in body
    assert body["summary"]["traces"]["count"]["current"] == 1204.0
    assert "_project_id" not in body

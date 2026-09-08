"""Unit tests for the ``read`` tool — dispatch, name-lookup, errors.

Uses a duck-typed ``FakeOpikClient`` so we can exercise the registry's
fetcher functions without spinning up httpx mocks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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

    async def get_project(self, project_id: str) -> dict[str, Any]:
        if project_id not in self.projects_by_id:
            raise OpikNotFoundError(f"project {project_id!r} not found (404).")
        return self.projects_by_id[project_id]

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
        trace_id: str,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 100,
    ) -> dict[str, Any]:
        content = self.trace_spans.get(trace_id, [])
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
        from_date="2026-09-01",
        to_date="2026-09-08",
        client=fake,
    )
    assert fake.last_issue_kwargs == {
        "project_id": "p-9",
        "from_date": "2026-09-01",
        "to_date": "2026-09-08",
    }


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
async def test_read_issue_ambiguous_project_name_lists_candidates() -> None:
    fake = _issue_fake()
    fake.projects_by_name = {"demo": [{"id": "p-1", "name": "demo"}, {"id": "p-2", "name": "demo"}]}
    with pytest.raises(ToolError) as exc:
        await run_read("agent_insights_issue", ISSUE, project_name="demo", client=fake)
    msg = str(exc.value)
    assert "p-1" in msg and "p-2" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_issue_unknown_project_name_is_a_clear_error() -> None:
    fake = _issue_fake()
    with pytest.raises(ToolError) as exc:
        await run_read("agent_insights_issue", ISSUE, project_name="ghost", client=fake)
    assert "No project named 'ghost'" in str(exc.value)
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
            from_date="2026-09-09",
            to_date="2026-09-01",
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

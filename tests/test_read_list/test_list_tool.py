"""Unit tests for the ``list`` tool — table shape, required kwargs, pagination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.opik_client import OpikValidationError
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.list_tool import run_list


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeOpikClient:
    """Just enough surface for the list tool to drive the registry."""

    projects: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    experiments: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    prompts: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    test_suites: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    traces: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    test_suite_items: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    prompt_versions: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    threads: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    issues: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    score_names: dict[str, Any] = field(default_factory=lambda: {"scores": []})
    automation_rules: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})

    last_kwargs: dict[str, Any] = field(default_factory=dict)

    project_lookups: int = 0
    fail_issues_with: Exception | None = None
    # Credential identity the project-name cache keys on; None mimics a fake
    # with no config, as every other test here has.
    _base_url: str | None = None
    _workspace: str | None = None
    _api_key: str | None = None

    async def list_agent_insights_issues(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        if self.fail_issues_with is not None:
            raise self.fail_issues_with
        return self.issues

    async def list_projects(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        self.project_lookups += 1
        return self.projects

    async def list_project_score_names(self, project_id: str, /) -> dict[str, Any]:
        self.last_kwargs = {"project_id": project_id}
        return self.score_names

    async def list_project_token_usage_names(self, project_id: str, /) -> dict[str, Any]:
        self.last_kwargs = {"project_id": project_id}
        return {"names": []}

    async def list_automation_rules(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.automation_rules

    async def list_project_activities(self, _project_id: str, /, **_kw: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def get_project_metrics(self, _project_id: str, /, **_kw: Any) -> dict[str, Any]:
        return {"results": []}

    async def list_experiments(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.experiments

    async def list_prompts(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.prompts

    async def list_test_suites(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.test_suites

    async def list_traces(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.traces

    async def list_threads(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.threads

    async def list_test_suite_items(self, test_suite_id: str, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = {"test_suite_id": test_suite_id, **kw}
        return self.test_suite_items

    async def list_prompt_versions(self, prompt_id: str, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = {"prompt_id": prompt_id, **kw}
        return self.prompt_versions

    async def list_spans(self, **_: Any) -> dict[str, Any]:
        # Not exercised by the list tool (span has no list_fn) — included to
        # satisfy the OpikListClient Protocol structurally.
        return {"content": [], "page": 1, "size": 0, "total": 0}


# --- table format --------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_projects_renders_pipe_delimited_table() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [
                {"id": "p-1", "name": "demo", "created_at": "2026-01-01"},
                {"id": "p-2", "name": "other", "created_at": "2026-02-01"},
            ],
            "total": 2,
        }
    )
    out = await run_list("project", client=fake)
    assert "Found 2 projects" in out
    assert "id | name | created_at" in out
    assert "p-1 | demo | 2026-01-01" in out
    assert "p-2 | other | 2026-02-01" in out


@pytest.mark.anyio
async def test_list_renders_empty_state_message() -> None:
    out = await run_list("project", client=FakeOpikClient())
    assert "No projects found" in out


@pytest.mark.anyio
async def test_list_with_name_filter_in_header_and_empty_message() -> None:
    fake = FakeOpikClient(experiments={"content": [], "total": 0})
    out = await run_list("experiment", name="zzz", client=fake)
    assert "No experiments matching 'zzz' found" in out
    assert fake.last_kwargs.get("name") == "zzz"


@pytest.mark.anyio
async def test_list_appends_pagination_hint_when_more_results() -> None:
    fake = FakeOpikClient(
        projects={"content": [{"id": "p-1", "name": "a"}], "total": 50},
    )
    out = await run_list("project", page=1, size=10, client=fake)
    assert "Use page=2 for next 10 results" in out


@pytest.mark.anyio
async def test_list_truncates_long_values_at_sixty_chars() -> None:
    fake = FakeOpikClient(
        projects={"content": [{"id": "p-1", "name": "x" * 100}], "total": 1},
    )
    out = await run_list("project", client=fake)
    assert "..." in out
    assert "x" * 60 not in out  # truncated form is 57 chars + "..."


# --- required kwargs ----------------------------------------------------- #


@pytest.mark.anyio
async def test_list_traces_requires_project_id() -> None:
    with pytest.raises(ToolError, match="requires project_id"):
        await run_list("trace", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_traces_with_project_id_forwards_kwarg() -> None:
    fake = FakeOpikClient(traces={"content": [], "total": 0})
    await run_list("trace", project_id="p-1", client=fake)
    assert fake.last_kwargs.get("project_id") == "p-1"


@pytest.mark.anyio
async def test_list_test_suite_items_requires_test_suite_id() -> None:
    with pytest.raises(ToolError, match="requires test_suite_id"):
        await run_list("test_suite_item", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_prompt_versions_requires_prompt_id() -> None:
    with pytest.raises(ToolError, match="requires prompt_id"):
        await run_list("prompt_version", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_threads_requires_project_id() -> None:
    with pytest.raises(ToolError, match="requires project_id"):
        await run_list("thread", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_threads_accepts_project_name_alternative() -> None:
    """project_name satisfies the project requirement (no UUID round-trip)."""
    fake = FakeOpikClient(threads={"content": [{"id": "th-1", "status": "active"}], "total": 1})
    out = await run_list("thread", project_name="support-bot", client=fake)
    assert fake.last_kwargs.get("project_name") == "support-bot"
    assert "project_id" not in fake.last_kwargs
    assert "th-1" in out


@pytest.mark.anyio
async def test_list_traces_accepts_project_name_alternative() -> None:
    fake = FakeOpikClient(traces={"content": [], "total": 0})
    await run_list("trace", project_name="support-bot", client=fake)
    assert fake.last_kwargs.get("project_name") == "support-bot"


@pytest.mark.anyio
async def test_list_thread_missing_project_error_mentions_name() -> None:
    with pytest.raises(ToolError, match=r"project_id \(or project_name\)"):
        await run_list("thread", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_project_name_not_forwarded_to_workspace_wide_list() -> None:
    """project_name must not leak into a non-project-scoped list_fn as a kwarg."""
    fake = FakeOpikClient(experiments={"content": [], "total": 0})
    await run_list("experiment", project_name="ignored", client=fake)
    assert "project_name" not in fake.last_kwargs


@pytest.mark.anyio
async def test_list_threads_renders_thread_columns() -> None:
    fake = FakeOpikClient(
        threads={
            "content": [
                {
                    "id": "conv-1",
                    "status": "active",
                    "number_of_messages": 4,
                    "last_updated_at": "2026-01-02",
                }
            ],
            "total": 1,
        }
    )
    out = await run_list("thread", project_id="p-1", client=fake)
    assert fake.last_kwargs.get("project_id") == "p-1"
    # id column carries the thread identifier; extras render status + counts.
    assert "conv-1" in out
    assert "status" in out
    assert "number_of_messages" in out
    assert "4" in out


# --- entity-type validation ---------------------------------------------- #


@pytest.mark.anyio
async def test_list_rejects_unknown_entity_type() -> None:
    with pytest.raises(ToolError, match="Cannot list 'widget'"):
        await run_list("widget", client=FakeOpikClient())


# --- size / page clamping ------------------------------------------------ #


@pytest.mark.anyio
async def test_list_clamps_size_to_max() -> None:
    fake = FakeOpikClient()
    await run_list("project", size=500, client=fake)
    assert fake.last_kwargs.get("size") == 100


@pytest.mark.anyio
async def test_list_unknown_entity_type_chains_typed_cause() -> None:
    """``list('wat')`` raises ToolError, but the cause must be the typed
    EntityArgValidationError so the analytics wrapper buckets it as
    validation/400 rather than unknown."""
    with pytest.raises(ToolError) as ei:
        await run_list("not_a_real_type")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_list_missing_required_kwarg_chains_typed_cause() -> None:
    """``list('trace')`` without ``project_id`` raises ToolError; the cause
    must be the typed EntityArgValidationError. Same chaining contract."""
    with pytest.raises(ToolError) as ei:
        await run_list("trace")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)


# --- entity-specific kwargs are registry-gated --------------------------- #


@pytest.mark.anyio
async def test_list_forwards_only_kwargs_the_entity_declares() -> None:
    """Parent ids meant for other entities never reach a workspace-wide list_fn.

    ``list('project', project_id=…, test_suite_id=…, prompt_id=…)`` is a
    confused call, but it must degrade to a plain project list rather than
    blow up the client with unexpected kwargs."""
    fake = FakeOpikClient(projects={"content": [{"id": "p-1", "name": "a"}], "total": 1})
    out = await run_list(
        "project", project_id="p-1", test_suite_id="ts-1", prompt_id="pr-1", client=fake
    )
    assert "p-1" in out
    assert set(fake.last_kwargs) == {"page", "size"}


# --- agent_insights_issue (Diagnostics) ---------------------------------- #

ISSUE_ROW = {
    "id": "is-1",
    "name": "Tool call loop on weather lookup",
    "severity": "high",
    "status": "open",
    "total_occurrences": 300,
    "latest_count": 12,
    "last_seen": "2026-09-07",
    "cause": "The agent retries the same tool call when the API times out.",
    "suggested_fix": "Cap retries at 2.",
}


@pytest.mark.anyio
async def test_list_issues_renders_diagnostics_columns_in_backend_order() -> None:
    second = {**ISSUE_ROW, "id": "is-2", "name": "Empty answer", "severity": "low"}
    fake = FakeOpikClient(issues={"content": [ISSUE_ROW, second], "total": 2})
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake)
    lines = out.splitlines()
    first = "is-1 | Tool call loop on weather lookup | high | open | 300 | 12 | 2026-09-07"
    second_row = "is-2 | Empty answer | low | open | 300 | 12 | 2026-09-07"
    assert "id | name | severity | status | total_occurrences | latest_count | last_seen" in lines
    assert first in lines
    assert lines.index(first) < lines.index(second_row)
    # Long prose stays out of the table — that is what read() is for.
    assert "Cap retries" not in out
    assert fake.last_kwargs.get("project_id") == "p-1"
    assert "sorting" not in fake.last_kwargs


@pytest.mark.anyio
async def test_list_issues_defaults_to_open_status() -> None:
    fake = FakeOpikClient()
    await run_list("agent_insights_issue", project_id="p-1", client=fake)
    assert fake.last_kwargs.get("status") == "open"


@pytest.mark.anyio
async def test_list_issues_forwards_explicit_status() -> None:
    fake = FakeOpikClient()
    await run_list("agent_insights_issue", project_id="p-1", status="resolved", client=fake)
    assert fake.last_kwargs.get("status") == "resolved"


@pytest.mark.anyio
async def test_list_issues_forwards_window_only_when_given() -> None:
    fake = FakeOpikClient()
    await run_list("agent_insights_issue", project_id="p-1", client=fake)
    assert "from_date" not in fake.last_kwargs
    assert "to_date" not in fake.last_kwargs

    # The same since/until vocabulary as traces, truncated to UTC report days
    # because the Diagnostics backend aggregates per day.
    out = await run_list(
        "agent_insights_issue",
        project_id="p-1",
        since="2026-09-01T15:30:00Z",
        until="2026-09-08T02:00:00+02:00",
        client=fake,
    )
    assert fake.last_kwargs.get("from_date") == "2026-09-01"
    assert fake.last_kwargs.get("to_date") == "2026-09-08"
    assert "from_time" not in fake.last_kwargs
    assert "since: 2026-09-01" in out
    assert "until: 2026-09-08" in out


@pytest.mark.anyio
async def test_list_issues_accept_relative_window() -> None:
    fake = FakeOpikClient()
    await run_list("agent_insights_issue", project_id="p-1", since="7d", client=fake)
    from_date = fake.last_kwargs.get("from_date")
    assert isinstance(from_date, str) and len(from_date) == 10
    assert "to_date" not in fake.last_kwargs


@pytest.mark.anyio
async def test_list_issues_inverted_window_rejected_before_backend() -> None:
    fake = FakeOpikClient()
    with pytest.raises(ToolError, match="before since"):
        await run_list(
            "agent_insights_issue",
            project_id="p-1",
            since="2026-09-09T00:00:00Z",
            until="2026-09-01T00:00:00Z",
            client=fake,
        )
    assert fake.last_kwargs == {}


@pytest.mark.anyio
async def test_list_issues_requires_project_scope() -> None:
    with pytest.raises(ToolError, match=r"requires project_id \(or project_name\)"):
        await run_list("agent_insights_issue", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_issues_ignores_name_filter() -> None:
    fake = FakeOpikClient()
    await run_list("agent_insights_issue", project_id="p-1", name="loop", client=fake)
    assert "name" not in fake.last_kwargs


@pytest.mark.anyio
async def test_list_issue_filters_not_forwarded_to_other_entities() -> None:
    fake = FakeOpikClient(projects={"content": [{"id": "p-1", "name": "a"}], "total": 1})
    await run_list("project", status="resolved", client=fake)
    assert set(fake.last_kwargs) == {"page", "size"}


@pytest.mark.anyio
async def test_list_issues_bad_window_surfaces_backend_validation_message() -> None:
    fake = FakeOpikClient(
        fail_issues_with=OpikValidationError(
            "Opik rejected the request body (400) for agent insights issues — "
            "Parameter 'from_date' must not be after 'to_date'"
        )
    )
    with pytest.raises(ToolError) as exc:
        await run_list(
            "agent_insights_issue",
            project_id="p-1",
            since="2026-09-01T00:00:00Z",
            until="2026-09-08T00:00:00Z",
            client=fake,
        )
    assert "from_date" in str(exc.value)
    assert isinstance(exc.value.__cause__, OpikValidationError)


@pytest.mark.anyio
async def test_list_issues_ambiguous_project_name_lists_candidate_names() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [{"id": "p-1", "name": "demo"}, {"id": "p-2", "name": "demo"}],
            "total": 2,
        }
    )
    with pytest.raises(ToolError) as exc:
        await run_list("agent_insights_issue", project_name="demo", client=fake)
    assert "project_id=p-1, name='demo'" in str(exc.value)


@pytest.mark.anyio
async def test_list_issues_empty_state() -> None:
    out = await run_list("agent_insights_issue", project_id="p-1", client=FakeOpikClient())
    assert "No agent_insights_issues found" in out


# --- project_name resolution (the backend takes project_id only) --------- #


@pytest.mark.anyio
async def test_list_issues_resolves_exact_project_name_to_id() -> None:
    """The projects endpoint is a substring search; only the exact name counts."""
    fake = FakeOpikClient(
        projects={
            "content": [
                {"id": "p-demo-2", "name": "demo-2"},
                {"id": "p-demo", "name": "demo"},
            ],
            "total": 2,
        },
        issues={"content": [ISSUE_ROW], "total": 1},
    )
    out = await run_list("agent_insights_issue", project_name="demo", client=fake)
    assert "is-1" in out
    assert fake.last_kwargs.get("project_id") == "p-demo"
    assert "project_name" not in fake.last_kwargs


@pytest.mark.anyio
async def test_list_issues_resolves_project_name_case_insensitively_like_the_backend() -> None:
    """list('trace', project_name='Support-Agent-Demo') succeeds because the
    backend matches project names case-insensitively; the same argument on the
    issue entity must not fail. Exact case still wins when both exist."""
    fake = FakeOpikClient(
        projects={"content": [{"id": "p-demo", "name": "support-agent-demo"}], "total": 1},
        issues={"content": [ISSUE_ROW], "total": 1},
    )
    out = await run_list("agent_insights_issue", project_name="Support-Agent-Demo", client=fake)
    assert "is-1" in out
    assert fake.last_kwargs.get("project_id") == "p-demo"


@pytest.mark.anyio
async def test_list_issues_exact_case_beats_case_insensitive_match() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [{"id": "p-upper", "name": "Demo"}, {"id": "p-lower", "name": "demo"}],
            "total": 2,
        }
    )
    await run_list("agent_insights_issue", project_name="demo", client=fake)
    assert fake.last_kwargs.get("project_id") == "p-lower"


@pytest.mark.anyio
async def test_list_issues_ambiguous_case_insensitive_match_lists_candidates() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [{"id": "p-upper", "name": "Demo"}, {"id": "p-lower", "name": "demo"}],
            "total": 2,
        }
    )
    with pytest.raises(ToolError) as exc:
        await run_list("agent_insights_issue", project_name="DEMO", client=fake)
    msg = str(exc.value)
    assert "p-upper" in msg and "p-lower" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_list_issues_ambiguous_project_name_lists_candidates() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [{"id": "p-1", "name": "demo"}, {"id": "p-2", "name": "demo"}],
            "total": 2,
        }
    )
    with pytest.raises(ToolError) as exc:
        await run_list("agent_insights_issue", project_name="demo", client=fake)
    msg = str(exc.value)
    assert "p-1" in msg
    assert "p-2" in msg
    assert "project_id" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_list_issues_unknown_project_name_suggests_the_closest_one() -> None:
    """Same recovery as a misspelled project_name on a trace list (#185): the
    message names the closest existing project and lists the rest."""
    fake = FakeOpikClient(
        projects={
            "content": [
                {"id": "p-1", "name": "support-agent-demo"},
                {"id": "p-2", "name": "probe"},
            ],
            "total": 2,
        }
    )
    with pytest.raises(ToolError) as exc:
        await run_list("agent_insights_issue", project_name="suport-agent-demo", client=fake)
    msg = str(exc.value)
    assert "Project 'suport-agent-demo' not found." in msg
    assert "Did you mean 'support-agent-demo'?" in msg
    assert "Projects: support-agent-demo, probe" in msg
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_list_issues_unknown_project_name_without_a_close_match() -> None:
    fake = FakeOpikClient(projects={"content": [{"id": "p-1", "name": "probe"}], "total": 1})
    with pytest.raises(ToolError) as exc:
        await run_list("agent_insights_issue", project_name="demo", client=fake)
    msg = str(exc.value)
    assert "Project 'demo' not found." in msg
    assert "Did you mean" not in msg
    assert "Projects: probe" in msg


@pytest.mark.anyio
async def test_list_issues_project_name_resolved_once_per_process() -> None:
    """Every call with project_name used to pay a second round trip. The
    resolved id is cached, so the follow-up call skips the projects lookup."""
    fake = FakeOpikClient(projects={"content": [{"id": "p-demo", "name": "demo"}], "total": 1})
    await run_list("agent_insights_issue", project_name="demo", client=fake)
    await run_list("agent_insights_issue", project_name="demo", status="resolved", client=fake)
    assert fake.project_lookups == 1
    assert fake.last_kwargs.get("project_id") == "p-demo"


@pytest.mark.anyio
async def test_list_issues_project_name_cache_is_per_credential() -> None:
    """Hosted OAuth passthrough: one process, many bearers, and the client's
    workspace may be unknown (it lives server-side). Two tenants asking for
    the same project name must never share a resolved id."""
    tenant_a = FakeOpikClient(projects={"content": [{"id": "p-a", "name": "demo"}], "total": 1})
    tenant_a._base_url = "https://opik.test/api"
    tenant_a._workspace = None
    tenant_a._api_key = "Bearer opik_mcp_at_aaa"
    tenant_b = FakeOpikClient(projects={"content": [{"id": "p-b", "name": "demo"}], "total": 1})
    tenant_b._base_url = "https://opik.test/api"
    tenant_b._workspace = None
    tenant_b._api_key = "Bearer opik_mcp_at_bbb"

    await run_list("agent_insights_issue", project_name="demo", client=tenant_a)
    await run_list("agent_insights_issue", project_name="demo", client=tenant_b)
    assert tenant_a.last_kwargs.get("project_id") == "p-a"
    assert tenant_b.last_kwargs.get("project_id") == "p-b"
    assert tenant_b.project_lookups == 1

    # Same credential again: served from the cache.
    same_as_a = FakeOpikClient(projects={"content": [], "total": 0})
    same_as_a._base_url = "https://opik.test/api"
    same_as_a._workspace = None
    same_as_a._api_key = "Bearer opik_mcp_at_aaa"
    await run_list("agent_insights_issue", project_name="demo", client=same_as_a)
    assert same_as_a.project_lookups == 0
    assert same_as_a.last_kwargs.get("project_id") == "p-a"


@pytest.mark.anyio
async def test_list_issues_project_name_cache_is_per_name() -> None:
    fake = FakeOpikClient(projects={"content": [{"id": "p-demo", "name": "demo"}], "total": 1})
    await run_list("agent_insights_issue", project_name="demo", client=fake)
    fake.projects = {"content": [{"id": "p-other", "name": "other"}], "total": 1}
    await run_list("agent_insights_issue", project_name="other", client=fake)
    assert fake.project_lookups == 2
    assert fake.last_kwargs.get("project_id") == "p-other"


@pytest.mark.anyio
async def test_list_issues_unresolved_project_name_is_not_cached() -> None:
    """A miss must not be remembered: the project may be created a moment later."""
    fake = FakeOpikClient()
    with pytest.raises(ToolError):
        await run_list("agent_insights_issue", project_name="demo", client=fake)
    # A miss costs two calls: the filtered lookup and the unfiltered one that
    # builds the did-you-mean. Neither result is remembered.
    lookups_after_miss = fake.project_lookups
    assert lookups_after_miss == 2
    fake.projects = {"content": [{"id": "p-demo", "name": "demo"}], "total": 1}
    await run_list("agent_insights_issue", project_name="demo", client=fake)
    assert fake.project_lookups == lookups_after_miss + 1
    assert fake.last_kwargs.get("project_id") == "p-demo"


@pytest.mark.anyio
async def test_list_issues_project_id_wins_over_name_without_lookup() -> None:
    fake = FakeOpikClient()
    await run_list("agent_insights_issue", project_id="p-9", project_name="demo", client=fake)
    assert fake.last_kwargs.get("project_id") == "p-9"
    assert "project_name" not in fake.last_kwargs
    assert fake.project_lookups == 0


@pytest.mark.anyio
async def test_list_forwards_declared_parent_id_to_sub_collection() -> None:
    fake = FakeOpikClient(prompt_versions={"content": [{"id": "v-1"}], "total": 1})
    await run_list("prompt_version", prompt_id="pr-1", test_suite_id="ts-1", client=fake)
    assert fake.last_kwargs.get("prompt_id") == "pr-1"
    assert "test_suite_id" not in fake.last_kwargs


# --- project vocabulary: score names and online rules -------------------- #
#
# Where "the first 25 of 213" leads. Neither was reachable before, in any tool,
# so a truncated project overview had nowhere to point.


@pytest.mark.anyio
async def test_list_score_names_returns_a_project_s_score_names() -> None:
    fake = FakeOpikClient(score_names={"scores": [{"name": "Hallucination"}, {"name": "tone"}]})
    out = await run_list("score_name", project_id="p-1", client=fake)
    assert "Hallucination" in out
    assert "tone" in out
    assert "Found 2 score_names" in out


@pytest.mark.anyio
async def test_list_score_names_has_no_id_column() -> None:
    """A score name has no id — the name is the identity. An always-empty id
    column would be a column of nothing on every row."""
    fake = FakeOpikClient(score_names={"scores": [{"name": "Hallucination"}]})
    out = await run_list("score_name", project_id="p-1", client=fake)
    columns = out.splitlines()[2]
    assert columns.strip() == "name"


@pytest.mark.anyio
async def test_list_score_names_says_the_names_span_every_entity_kind() -> None:
    """The endpoint has no entity_type predicate, so trace, span and thread
    names arrive together and a caller cannot tell which is which from a name.
    Saying so beats letting the agent assume they are all trace scores."""
    fake = FakeOpikClient(score_names={"scores": [{"name": "Hallucination"}]})
    out = await run_list("score_name", project_id="p-1", client=fake)
    assert "trace" in out and "span" in out and "thread" in out


@pytest.mark.anyio
async def test_list_score_names_does_not_invent_a_type() -> None:
    """The combined endpoint returns names only. A `type` column would be
    empty on every row, and filling it would be a guess."""
    fake = FakeOpikClient(score_names={"scores": [{"name": "Hallucination"}]})
    out = await run_list("score_name", project_id="p-1", client=fake)
    assert "type" not in out.splitlines()[2]


@pytest.mark.anyio
async def test_list_score_names_pages_even_though_the_backend_cannot() -> None:
    """Found by review: the whole set came back with `total` set to the whole
    set, so the table's own footer promised a page 2 that returned the same
    rows. The endpoint has no LIMIT, so the slice has to be ours."""
    fake = FakeOpikClient(score_names={"scores": [{"name": f"s-{i:02d}"} for i in range(30)]})
    first = await run_list("score_name", project_id="p-1", size=25, client=fake)
    assert "showing 25 of 30" in first
    assert "Use page=2" in first
    assert "s-24" in first
    assert "s-25" not in first

    second = await run_list("score_name", project_id="p-1", page=2, size=25, client=fake)
    assert "showing 5 of 30" in second
    assert "s-25" in second
    assert "s-24" not in second
    assert "Use page=3" not in second


@pytest.mark.anyio
async def test_list_score_names_offers_no_next_page_when_they_all_fit() -> None:
    fake = FakeOpikClient(score_names={"scores": [{"name": "only"}]})
    out = await run_list("score_name", project_id="p-1", client=fake)
    assert "page=" not in out


@pytest.mark.anyio
async def test_list_score_names_requires_project_scope() -> None:
    with pytest.raises(ToolError, match="project_id"):
        await run_list("score_name", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_score_names_empty_project_is_not_an_error() -> None:
    out = await run_list("score_name", project_id="p-1", client=FakeOpikClient())
    assert "No score_names found." in out


@pytest.mark.anyio
async def test_list_online_rules_shows_name_and_kind() -> None:
    fake = FakeOpikClient(
        automation_rules={
            "content": [{"id": "r-1", "name": "judge", "type": "llm_as_judge", "enabled": True}],
            "total": 1,
        }
    )
    out = await run_list("online_rule", project_id="p-1", client=fake)
    assert "judge" in out
    assert "llm_as_judge" in out
    assert fake.last_kwargs.get("project_id") == "p-1"


@pytest.mark.anyio
async def test_list_online_rules_paginates_like_every_other_list() -> None:
    fake = FakeOpikClient(
        automation_rules={
            "content": [{"id": f"r-{i}", "name": f"rule-{i}"} for i in range(2)],
            "total": 7,
        }
    )
    out = await run_list("online_rule", project_id="p-1", size=2, client=fake)
    assert "Use page=2" in out
    assert fake.last_kwargs.get("size") == 2


@pytest.mark.anyio
async def test_list_online_rules_requires_project_scope() -> None:
    with pytest.raises(ToolError, match="project_id"):
        await run_list("online_rule", client=FakeOpikClient())

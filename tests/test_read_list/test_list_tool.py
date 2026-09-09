"""Unit tests for the ``list`` tool — table shape, required kwargs, pagination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikNotFoundError, OpikServerError, OpikValidationError
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

    last_kwargs: dict[str, Any] = field(default_factory=dict)

    project_lookups: int = 0
    fail_issues_with: Exception | None = None
    # Diagnostics job for the project: None mimics the backend's 404 (never
    # enabled); an exception in ``job_error`` mimics a failed side lookup.
    job: dict[str, Any] | None = None
    job_error: Exception | None = None
    job_reads: int = 0
    # Service toggles as the backend serves them; ``toggles_error`` mimics a
    # failed lookup, which must fail open (Diagnostics assumed available).
    toggles: dict[str, Any] = field(default_factory=lambda: {"ollieEnabled": True})
    toggles_error: Exception | None = None
    toggles_reads: int = 0
    projects_error: Exception | None = None
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

    async def get_agent_insights_job(self, project_id: str) -> dict[str, Any]:
        self.job_reads += 1
        if self.job_error is not None:
            raise self.job_error
        if self.job is None:
            raise OpikNotFoundError(
                f"agent insights job for project {project_id!r} not found (404)."
            )
        return self.job

    async def get_service_toggles(self) -> dict[str, Any]:
        self.toggles_reads += 1
        if self.toggles_error is not None:
            raise self.toggles_error
        return self.toggles

    async def list_projects(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        self.project_lookups += 1
        if self.projects_error is not None:
            raise self.projects_error
        return self.projects

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


# --- the empty issue list explains the project's Diagnostics state -------- #

_UI = Settings(opik_api_key="k", comet_workspace="demo-ws", opik_url="https://opik.test/api")
_NO_UI = Settings(opik_api_key="k", comet_workspace="demo-ws", opik_url=None, comet_url_override="")


def _ago(hours: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _job(status: str = "enabled", **fields: Any) -> dict[str, Any]:
    return {"id": "job-1", "project_id": "p-1", "status": status, **fields}


@pytest.mark.anyio
async def test_empty_issues_say_diagnostics_never_enabled_and_how_to_enable() -> None:
    """A 404 on the job means nobody ever turned Diagnostics on for the project.
    The reply says so, names both write operations, and links the page."""
    fake = FakeOpikClient()
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "No agent_insights_issues found." in out
    assert "Diagnostics is not enabled for this project" in out
    assert "agent_insights_job.enable" in out
    assert "agent_insights_job.trigger" in out
    assert "https://opik.test/demo-ws/projects/p-1/diagnostics" in out
    assert fake.job_reads == 1


@pytest.mark.anyio
async def test_empty_issues_hint_shows_a_payload_the_write_tool_accepts() -> None:
    """The snippet is meant to be copied into write(), whose data is JSON —
    so it must be JSON, not Python kwargs."""
    fake = FakeOpikClient()
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert '{"project_id": "p-1"}' in out
    assert "project_id='p-1'" not in out


@pytest.mark.anyio
async def test_empty_issues_say_diagnostics_is_turned_off() -> None:
    fake = FakeOpikClient(job=_job("disabled", last_scan_at=_ago(96)))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is turned off for this project" in out
    assert "agent_insights_job.enable" in out


@pytest.mark.anyio
async def test_empty_issues_disabled_state_tolerates_backend_casing() -> None:
    fake = FakeOpikClient(job=_job("DISABLED"))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is turned off for this project" in out


@pytest.mark.anyio
async def test_empty_issues_ignore_an_unknown_status_in_the_all_clear() -> None:
    """``status`` is enum-constrained at the tool boundary; a direct caller's
    string must not be interpolated into the reply."""
    fake = FakeOpikClient(job=_job("enabled", last_scan_at=_ago(2)))
    out = await run_list(
        "agent_insights_issue", project_id="p-1", status="bogus", client=fake, settings=_UI
    )
    assert "No open issues" in out
    assert "bogus" not in out


@pytest.mark.anyio
async def test_empty_issues_say_enabled_but_never_scanned() -> None:
    """The job record carries no "run in flight" field (the UI tracks that in
    its own state), so a scan started a minute ago looks the same as none at
    all. Say both rather than telling the agent to trigger a duplicate."""
    fake = FakeOpikClient(job=_job("enabled"))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is enabled but has no completed scan yet" in out
    assert "may still be running" in out
    assert "agent_insights_job.trigger" in out
    assert "agent_insights_job.enable" not in out


@pytest.mark.anyio
async def test_empty_issues_all_clear_says_it_only_covers_the_window() -> None:
    """With since/until the page is narrowed, so "no open issues" is a claim
    about the window, not about the project."""
    fake = FakeOpikClient(job=_job("enabled", last_scan_at=_ago(2)))
    out = await run_list(
        "agent_insights_issue",
        project_id="p-1",
        since="7d",
        client=fake,
        settings=_UI,
    )
    assert "No open issues in the requested window." in out


@pytest.mark.anyio
async def test_empty_issues_stale_boundary_is_24_hours() -> None:
    fresh = FakeOpikClient(job=_job("enabled", last_scan_at=_ago(23)))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fresh, settings=_UI)
    assert "No open issues" in out
    stale = FakeOpikClient(job=_job("enabled", last_scan_at=_ago(25)))
    out = await run_list("agent_insights_issue", project_id="p-1", client=stale, settings=_UI)
    assert "older than a day" in out


@pytest.mark.anyio
async def test_empty_issues_disabled_state_does_not_invent_a_turn_off_date() -> None:
    """``last_updated_at`` is the row's mtime — a trigger or a scan moves it —
    so it cannot be reported as the date somebody turned Diagnostics off."""
    fake = FakeOpikClient(
        job=_job("disabled", last_updated_at="2026-09-05T10:00:00.000Z", last_scan_at=_ago(96))
    )
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is turned off for this project" in out
    assert "2026-09-05" not in out


@pytest.mark.anyio
async def test_issue_list_project_failure_is_a_tool_error_not_a_crash() -> None:
    """The list's own resolve fails first; the hint never runs. Either way the
    agent gets a ToolError, never a raw client exception."""
    fake = FakeOpikClient(projects_error=OpikServerError("projects down"))
    with pytest.raises(ToolError):
        await run_list("agent_insights_issue", project_name="demo", client=fake, settings=_UI)


@pytest.mark.anyio
async def test_empty_issues_say_last_scan_is_stale() -> None:
    fake = FakeOpikClient(job=_job("enabled", last_scan_at=_ago(72)))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "older than a day" in out
    assert "agent_insights_job.trigger" in out


@pytest.mark.anyio
async def test_empty_issues_with_fresh_scan_is_an_all_clear() -> None:
    """Only this state means nothing is wrong, so it says so and proposes nothing."""
    scanned = _ago(2)
    fake = FakeOpikClient(job=_job("enabled", last_scan_at=scanned))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "No open issues. Last scan:" in out
    assert scanned[:16] in out  # to the minute, UTC
    assert "agent_insights_job" not in out
    assert "https://opik.test/demo-ws/projects/p-1/diagnostics" in out


@pytest.mark.anyio
async def test_empty_resolved_issues_name_the_status_in_the_all_clear() -> None:
    fake = FakeOpikClient(job=_job("enabled", last_scan_at=_ago(2)))
    out = await run_list(
        "agent_insights_issue", project_id="p-1", status="resolved", client=fake, settings=_UI
    )
    assert "No resolved issues. Last scan:" in out


@pytest.mark.anyio
async def test_empty_issues_quote_the_last_failure_reason() -> None:
    fake = FakeOpikClient(
        job=_job(
            "enabled",
            last_scan_at=_ago(30),
            last_failed_at=_ago(1),
            last_failure_reason="did_not_start",
            last_failure_detail="Connection refused: react-svc:8080",
        )
    )
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "The last run failed: did_not_start (Connection refused: react-svc:8080)." in out


@pytest.mark.anyio
async def test_empty_issues_hint_omits_link_when_ui_unknown() -> None:
    fake = FakeOpikClient()
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_NO_UI)
    assert "Diagnostics is not enabled for this project" in out
    assert "https://" not in out


@pytest.mark.anyio
async def test_empty_issues_hint_degrades_when_job_lookup_fails() -> None:
    """The hint decorates the answer; it must never replace it with an error."""
    fake = FakeOpikClient(job_error=OpikServerError("boom"))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert out.strip() == "No agent_insights_issues found."


@pytest.mark.anyio
async def test_empty_issues_hint_resolves_project_name_once() -> None:
    fake = FakeOpikClient(projects={"content": [{"id": "p-demo", "name": "demo"}], "total": 1})
    out = await run_list("agent_insights_issue", project_name="demo", client=fake, settings=_UI)
    assert "https://opik.test/demo-ws/projects/p-demo/diagnostics" in out
    assert fake.project_lookups == 1


@pytest.mark.anyio
async def test_empty_issues_say_diagnostics_unavailable_on_this_deployment() -> None:
    """With Ollie off nothing will ever scan, so proposing enable would be a
    lie: the reply says the deployment has no Diagnostics and stops there."""
    fake = FakeOpikClient(toggles={"ollieEnabled": False})
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is not available on this deployment" in out
    assert "agent_insights_job" not in out
    assert fake.job_reads == 0


@pytest.mark.anyio
async def test_deployment_gate_accepts_the_ui_field_spelling() -> None:
    fake = FakeOpikClient(toggles={"ollie_enabled": False})
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "not available on this deployment" in out


@pytest.mark.anyio
async def test_deployment_gate_treats_a_missing_field_as_available() -> None:
    """An older backend without the toggle must keep today's behaviour."""
    fake = FakeOpikClient(toggles={"guardrailsEnabled": True})
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is not enabled for this project" in out


@pytest.mark.anyio
async def test_deployment_gate_fails_open_when_toggles_cannot_be_read() -> None:
    fake = FakeOpikClient(toggles_error=OpikServerError("boom"))
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is not enabled for this project" in out


@pytest.mark.anyio
async def test_deployment_gate_reads_the_toggle_per_call() -> None:
    """Not cached: the gate is consulted only for Diagnostics work, so a live
    answer is worth more than the saved request."""
    fake = FakeOpikClient(toggles={"ollieEnabled": True})
    await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    await run_list("agent_insights_issue", project_id="p-2", client=fake, settings=_UI)
    assert fake.toggles_reads == 2


@pytest.mark.anyio
async def test_deployment_gate_sees_ollie_switched_on_mid_session() -> None:
    """The reason the answer is not remembered: refusing for minutes after an
    operator switches Ollie on would read as permanent."""
    fake = FakeOpikClient(toggles={"ollieEnabled": False})
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "not available on this deployment" in out
    fake.toggles = {"ollieEnabled": True}
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "Diagnostics is not enabled for this project" in out
    assert fake.toggles_reads == 2


@pytest.mark.anyio
async def test_deployment_gate_failure_does_not_stick() -> None:
    fake = FakeOpikClient(toggles_error=OpikServerError("boom"))
    await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    fake.toggles_error = None
    fake.toggles = {"ollieEnabled": False}
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "not available on this deployment" in out
    assert fake.toggles_reads == 2


@pytest.mark.anyio
async def test_non_empty_issues_read_the_job_once_for_coverage() -> None:
    """One extra GET buys the answer's as-of date. Without it a week-old report
    reads as current, which is the false all-clear again in a new costume."""
    fake = FakeOpikClient(
        issues={"content": [ISSUE_ROW], "total": 1}, job=_job(last_scan_at=_ago(1))
    )
    await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert fake.job_reads == 1


@pytest.mark.anyio
async def test_issue_list_names_what_the_report_covers() -> None:
    fake = FakeOpikClient(
        issues={"content": [ISSUE_ROW], "total": 1}, job=_job(last_scan_at=_ago(0.2))
    )
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "is-1" in out
    assert "Report covers data through" in out
    # Inside the grace window there is nothing to route: no gap, no fallback.
    assert "not in it" not in out


@pytest.mark.anyio
async def test_issue_list_routes_to_traces_when_the_window_outruns_the_last_scan() -> None:
    """The case that motivated this: asked for a week, scanned through
    yesterday. The uncovered tail is named and the fallback given, so the
    grouped list is not reported as the whole answer."""
    fake = FakeOpikClient(
        issues={"content": [ISSUE_ROW], "total": 1}, job=_job(last_scan_at=_ago(20))
    )
    out = await run_list(
        "agent_insights_issue", project_id="p-1", since="7d", client=fake, settings=_UI
    )
    assert "Report covers data through" in out
    assert "20h" in out and "not in it" in out
    assert "agent_insights_job.trigger" in out
    assert "list('trace'" in out


@pytest.mark.anyio
async def test_issue_list_does_not_offer_a_trigger_that_cannot_close_the_gap() -> None:
    """A trigger rescans the last 24 hours only, so for a wider gap it would
    leave the middle missing while reading as the fix."""
    fake = FakeOpikClient(
        issues={"content": [ISSUE_ROW], "total": 1}, job=_job(last_scan_at=_ago(96))
    )
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "4d" in out and "not in it" in out
    assert "list('trace'" in out
    assert "rescans the last 24 hours, so it cannot close this gap" in out


@pytest.mark.anyio
async def test_issue_list_reports_no_gap_when_the_window_ends_before_the_scan() -> None:
    """``until`` inside the scanned range asks for data the report fully
    covers, so there is nothing to route."""
    fake = FakeOpikClient(
        issues={"content": [ISSUE_ROW], "total": 1}, job=_job(last_scan_at=_ago(20))
    )
    out = await run_list(
        "agent_insights_issue",
        project_id="p-1",
        since="7d",
        until="24h",
        client=fake,
        settings=_UI,
    )
    assert "Report covers data through" in out
    assert "not in it" not in out


@pytest.mark.anyio
async def test_issue_list_coverage_note_is_dropped_when_the_job_cannot_be_read() -> None:
    """Same rule as the empty-list hint: a failed side lookup must never turn
    an answered list into an error or a guess."""
    fake = FakeOpikClient(
        issues={"content": [ISSUE_ROW], "total": 1}, job_error=OpikServerError("boom")
    )
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "is-1" in out
    assert "Report covers" not in out


@pytest.mark.anyio
async def test_issue_list_coverage_note_survives_a_job_without_a_scan_time() -> None:
    fake = FakeOpikClient(issues={"content": [ISSUE_ROW], "total": 1}, job=_job())
    out = await run_list("agent_insights_issue", project_id="p-1", client=fake, settings=_UI)
    assert "is-1" in out
    assert "Report covers" not in out


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

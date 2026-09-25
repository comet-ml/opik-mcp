"""Every write operation a real backend accepts, read back through the server.

Every record a write creates is named with this run's prefix and lives in
this run's own project, so no write touches what the read tests assert, and
the run deletes it all when it ends (``run_prefix`` in the conftest). That is
what lets the writes run against a shared cloud workspace too.

The thread and issue lifecycles act on a thread and an issue made for the
test. A Diagnostics issue has no write that creates one, so the test posts it
to the backend directly, the way the seed does.

The Diagnostics job operations need Ollie, which Opik cloud runs and an open
source backend does not; there they skip, since the server refuses them by
design.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import anyio
import pytest
from scripts.seed_e2e_backend import Backend, is_local

from tests.live.conftest import Answer, Live, new_id

pytestmark = [pytest.mark.live, pytest.mark.anyio]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


#: How long a write may take to show. Cloud applies writes asynchronously and
#: builds threads from an async listener; a local backend is near instant.
_SETTLE_S = 60


async def _eventually(check: Callable[[], Awaitable[bool]], what: str) -> None:
    """Derived state (a thread, its status) lands a moment after the write."""
    with anyio.move_on_after(_SETTLE_S):
        while not await check():
            await anyio.sleep(0.5)
        return
    pytest.fail(f"{what} did not happen within {_SETTLE_S}s")


async def _settled[T](fetch: Callable[[], Awaitable[T]], done: Callable[[T], bool]) -> T:
    """The first read that shows the write landed, or the last one tried.

    The test then asserts on what it got, so a write that never lands fails
    on the property it broke, with the value the backend returned.
    """
    value = await fetch()
    with anyio.move_on_after(_SETTLE_S):
        while not done(value):
            await anyio.sleep(0.5)
            value = await fetch()
    return value


def _body(answer: Answer, key: str) -> dict[str, object]:
    record = answer.record()
    body = record.get(key, record)
    assert isinstance(body, dict), f"{answer.call} has no {key}"
    return {str(k): v for k, v in body.items()}


async def _trace(mcp: Live, project: str, name: str, thread_id: str | None = None) -> str:
    trace_id = new_id()
    data: dict[str, object] = {
        "id": trace_id,
        "name": name,
        "project_name": project,
        "start_time": _now(),
        "input": {"question": "written by the live suite"},
    }
    if thread_id is not None:
        data["thread_id"] = thread_id
    await mcp.write("trace.create", data)

    # Cloud makes a new trace visible a moment after the write returns; a
    # score or comment sent before then is refused as not found.
    async def readable() -> bool:
        return not (await mcp.read("trace", trace_id)).is_error

    await _eventually(readable, f"trace {trace_id} becoming readable")
    return trace_id


async def _read_trace(mcp: Live, trace_id: str) -> dict[str, object]:
    return _body(await mcp.read("trace", trace_id), "trace")


async def _span_ids(mcp: Live, trace_id: str) -> list[object]:
    spans = (await mcp.read("trace", trace_id)).record()["spans"]
    assert isinstance(spans, list)
    return [s.get("id") for s in spans if isinstance(s, dict)]


async def _experiment_runs(mcp: Live, name: str) -> object:
    return (await mcp.read("experiment", name)).record()["trace_count"]


async def _project_id(mcp: Live, project: str) -> str:
    return (await mcp.list("project", name=project)).column("id")[0]


async def test_a_created_trace_reads_back_with_its_name(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-trace")
    trace = _body(await mcp.read("trace", trace_id), "trace")
    assert trace["name"] == f"{run_prefix}-trace"


async def test_a_trace_created_by_project_id_carries_a_link_to_its_page(
    mcp: Live, run_prefix: str
) -> None:
    # By id: a project name is not resolved to an id just to build a link.
    await _trace(mcp, run_prefix, f"{run_prefix}-first")
    trace_id = new_id()
    result = await mcp.write(
        "trace.create",
        {
            "id": trace_id,
            "name": f"{run_prefix}-linked",
            "project_id": await _project_id(mcp, run_prefix),
            "start_time": _now(),
        },
    )
    url = result.get("url")
    assert isinstance(url, str), f"no link in {result}"
    assert trace_id in url


async def test_an_updated_trace_reads_back_changed(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-before")
    await mcp.write(
        "trace.update",
        {"id": trace_id, "project_name": run_prefix, "tags": [f"{run_prefix}-tag"]},
    )
    trace = await _settled(
        lambda: _read_trace(mcp, trace_id), lambda t: t.get("tags") == [f"{run_prefix}-tag"]
    )
    assert trace["tags"] == [f"{run_prefix}-tag"]


async def test_a_created_span_hangs_under_its_trace(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-parent")
    span_id = new_id()
    await mcp.write(
        "span.create",
        {
            "id": span_id,
            "trace_id": trace_id,
            "name": f"{run_prefix}-span",
            "project_name": run_prefix,
            "start_time": _now(),
            "type": "tool",
        },
    )
    ids = await _settled(lambda: _span_ids(mcp, trace_id), lambda found: bool(found))
    assert ids == [span_id]


async def test_a_score_lands_on_its_trace(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-scored")
    await mcp.write(
        "score.create",
        {
            "target": "trace",
            "target_id": trace_id,
            "name": "live_suite",
            "value": 0.5,
            "project_name": run_prefix,
        },
    )
    trace = await _settled(
        lambda: _read_trace(mcp, trace_id), lambda t: bool(t.get("feedback_scores"))
    )
    scores = trace.get("feedback_scores")
    assert isinstance(scores, list)
    assert [(s.get("name"), s.get("value")) for s in scores if isinstance(s, dict)] == [
        ("live_suite", 0.5)
    ]


async def test_a_comment_lands_on_its_trace(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-commented")
    await mcp.write(
        "comment.create", {"target": "trace", "target_id": trace_id, "text": run_prefix}
    )
    trace = await _settled(lambda: _read_trace(mcp, trace_id), lambda t: bool(t.get("comments")))
    comments = trace.get("comments")
    assert isinstance(comments, list)
    assert [c.get("text") for c in comments if isinstance(c, dict)] == [run_prefix]


async def _dataset_with_items(mcp: Live, name: str, questions: list[str]) -> str:
    await mcp.write("dataset.create", {"name": name})
    await mcp.write(
        "dataset_item.upsert",
        {"dataset_name": name, "items": [{"data": {"q": q}} for q in questions]},
    )
    dataset_id = _body(await mcp.read("dataset", name), "dataset")["id"]
    assert isinstance(dataset_id, str)
    return dataset_id


async def test_a_dataset_and_its_items_are_created(mcp: Live, run_prefix: str) -> None:
    dataset_id = await _dataset_with_items(mcp, f"{run_prefix}-dataset", ["one", "two"])
    items = await mcp.list("dataset_item", dataset_id=dataset_id)
    assert sorted(items.column("data.q")) == ["one", "two"]


async def test_an_experiment_and_its_item_are_created(mcp: Live, run_prefix: str) -> None:
    dataset = f"{run_prefix}-experiment-dataset"
    dataset_id = await _dataset_with_items(mcp, dataset, ["only"])
    item_id = (await mcp.list("dataset_item", dataset_id=dataset_id)).column("id")[0]
    name = f"{run_prefix}-experiment"
    await mcp.write(
        "experiment.create",
        {"name": name, "dataset_name": dataset},
    )
    experiment = (await mcp.read("experiment", name)).record()
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-run")
    await mcp.write(
        "experiment_item.create",
        {
            "experiment_items": [
                {
                    "experiment_id": experiment["id"],
                    "dataset_item_id": item_id,
                    "trace_id": trace_id,
                }
            ]
        },
    )
    runs = await _settled(lambda: _experiment_runs(mcp, name), lambda count: count == 1)
    assert runs == 1


async def test_saved_prompt_versions_read_back_newest_first(mcp: Live, run_prefix: str) -> None:
    name = f"{run_prefix}-prompt"
    for template in ("first {{q}}", "second {{q}}"):
        await mcp.write("prompt_version.save", {"name": name, "template": template})
    versions = (await mcp.read("prompt", name)).record()["versions"]
    assert isinstance(versions, list)
    assert [v.get("template") for v in versions if isinstance(v, dict)] == [
        "second {{q}}",
        "first {{q}}",
    ]


async def test_closing_a_thread_makes_it_inactive_and_opening_it_active(
    mcp: Live, run_prefix: str
) -> None:
    thread_id = f"{run_prefix}-thread"
    for turn in range(2):
        await _trace(mcp, run_prefix, f"{run_prefix}-turn-{turn}", thread_id=thread_id)
    target = {"thread_id": thread_id, "project_name": run_prefix}

    async def status() -> object:
        answer = await mcp.read("thread", thread_id, project_name=run_prefix)
        return None if answer.is_error else _body(answer, "thread").get("status")

    async def exists() -> bool:
        return await status() is not None

    await _eventually(exists, "the thread appearing")
    await mcp.write("thread.close", target)

    async def inactive() -> bool:
        return await status() == "inactive"

    await _eventually(inactive, "the thread closing")
    await mcp.write("thread.open", target)

    async def active() -> bool:
        return await status() == "active"

    await _eventually(active, "the thread reopening")


#: The status each lifecycle operation leaves an issue in.
STATUS = {"resolve": "resolved", "close": "closed"}


@pytest.mark.parametrize("operation", ["resolve", "close"])
async def test_an_issue_moves_to_its_new_status_and_reopen_brings_it_back(
    mcp: Live, backend: Backend, run_prefix: str, operation: str
) -> None:
    await _trace(mcp, run_prefix, f"{run_prefix}-issue-host")
    project_id = await _project_id(mcp, run_prefix)
    issue_id = new_id()
    backend.call(
        "POST",
        "/agent-insights/issues",
        {
            "project_id": project_id,
            "report_day": _now()[:10],
            "issues": [
                {
                    "id": issue_id,
                    "name": f"{run_prefix}-issue-{operation}",
                    "severity": "low",
                    "count": 1,
                    "total_count": 1,
                    "users_impacted": 1,
                    "total_users": 1,
                }
            ],
        },
    )
    issue = {"issue_id": issue_id, "project_id": project_id}

    async def open_ids() -> set[str]:
        answer = await mcp.list("agent_insights_issue", project_id=project_id)
        # An empty list has no table to read, only a sentence saying so.
        return set(answer.column("id")) if "Found " in answer.text else set()

    assert issue_id in await open_ids()
    await mcp.write(f"agent_insights_issue.{operation}", issue)
    moved = await mcp.list("agent_insights_issue", project_id=project_id, status=STATUS[operation])
    assert (issue_id in await open_ids(), issue_id in moved.column("id")) == (False, True)
    await mcp.write("agent_insights_issue.reopen", issue)
    assert issue_id in await open_ids()


@pytest.mark.parametrize("operation", ["enable", "trigger"])
async def test_a_diagnostics_job_operation_is_accepted_where_ollie_runs(
    mcp: Live, backend: Backend, run_prefix: str, operation: str
) -> None:
    toggles = backend.call("GET", "/toggles/")
    flags = toggles if isinstance(toggles, dict) else {}
    # Both spellings: the server's own availability check accepts either.
    ollie = flags.get("ollie_enabled", flags.get("ollieEnabled"))
    if ollie is None and not is_local(backend.base_url):
        pytest.fail(f"cloud reports no Ollie toggle, so this test cannot tell: {sorted(flags)}")
    if ollie is not True:
        pytest.skip("this backend does not run Ollie, so Diagnostics jobs cannot run")
    await _trace(mcp, run_prefix, f"{run_prefix}-diagnosed")
    target = {"project_name": run_prefix}
    if operation == "trigger":
        await mcp.write("agent_insights_job.enable", target)
    result = await mcp.write(f"agent_insights_job.{operation}", target)
    assert isinstance(result.get("url"), str), f"no link in {result}"


async def _span(mcp: Live, project: str, trace_id: str, name: str) -> str:
    span_id = new_id()
    await mcp.write(
        "span.create",
        {
            "id": span_id,
            "trace_id": trace_id,
            "name": name,
            "project_name": project,
            "start_time": _now(),
            "type": "tool",
        },
    )
    await _settled(lambda: _span_ids(mcp, trace_id), lambda found: span_id in found)
    return span_id


async def _read_span(mcp: Live, span_id: str) -> dict[str, object]:
    return _body(await mcp.read("span", span_id), "span")


async def test_a_span_created_by_project_id_links_to_that_span(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-span-parent")
    span_id = new_id()
    result = await mcp.write(
        "span.create",
        {
            "id": span_id,
            "trace_id": trace_id,
            "name": f"{run_prefix}-linked-span",
            "project_id": await _project_id(mcp, run_prefix),
            "start_time": _now(),
            "type": "tool",
        },
    )
    url = result.get("url")
    assert isinstance(url, str), f"no link in {result}"
    assert (trace_id in url, span_id in url) == (True, True)


async def test_a_score_on_a_span_lands_on_that_span(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-span-scored")
    span_id = await _span(mcp, run_prefix, trace_id, f"{run_prefix}-scored-span")
    await mcp.write(
        "score.create",
        {
            "target": "span",
            "target_id": span_id,
            "name": "span_check",
            "value": 1.0,
            "project_name": run_prefix,
        },
    )
    span = await _settled(
        lambda: _read_span(mcp, span_id), lambda sp: bool(sp.get("feedback_scores"))
    )
    scores = span.get("feedback_scores")
    assert isinstance(scores, list)
    assert [(s.get("name"), s.get("value")) for s in scores if isinstance(s, dict)] == [
        ("span_check", 1.0)
    ]


async def test_a_comment_on_a_span_lands_on_that_span(mcp: Live, run_prefix: str) -> None:
    trace_id = await _trace(mcp, run_prefix, f"{run_prefix}-span-commented")
    span_id = await _span(mcp, run_prefix, trace_id, f"{run_prefix}-commented-span")
    await mcp.write("comment.create", {"target": "span", "target_id": span_id, "text": run_prefix})
    span = await _settled(lambda: _read_span(mcp, span_id), lambda sp: bool(sp.get("comments")))
    comments = span.get("comments")
    assert isinstance(comments, list)
    assert [c.get("text") for c in comments if isinstance(c, dict)] == [run_prefix]


async def test_a_score_on_a_thread_lands_on_that_thread(mcp: Live, run_prefix: str) -> None:
    thread_id = f"{run_prefix}-scored-thread"
    await _trace(mcp, run_prefix, f"{run_prefix}-thread-turn", thread_id=thread_id)

    async def thread() -> dict[str, object]:
        answer = await mcp.read("thread", thread_id, project_name=run_prefix)
        return {} if answer.is_error else _body(answer, "thread")

    await _settled(thread, bool)
    # Thread scores go through the batch route, so the write takes a list.
    await mcp.write(
        "score.create",
        [
            {
                "target": "thread",
                "target_id": thread_id,
                "name": "thread_check",
                "value": 1.0,
                "project_name": run_prefix,
            }
        ],
    )
    found = await _settled(thread, lambda t: bool(t.get("feedback_scores")))
    scores = found.get("feedback_scores")
    assert isinstance(scores, list)
    assert [(s.get("name"), s.get("value")) for s in scores if isinstance(s, dict)] == [
        ("thread_check", 1.0)
    ]

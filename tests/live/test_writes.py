"""Every write operation a real backend accepts, read back through the server.

Additive writes create records under this run's prefix in their own project,
so the seeded fixture's counts never move and a rerun on the same backend
still verifies. The two state changes, thread close/open and issue
resolve/close/reopen, act on records seeded for them and put them back.

The Diagnostics job operations are not here: they need Ollie, which an open
source backend does not run, and the server refuses them there by design.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import anyio
import pytest
from scripts.seed_e2e_backend import PREFIX, Manifest

from tests.live.conftest import Answer, Live, new_id

pytestmark = [pytest.mark.live, pytest.mark.anyio]

WRITES_PROJECT = f"{PREFIX}writes"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _eventually(check: Callable[[], Awaitable[bool]], what: str) -> None:
    """Derived state (a thread's status) lands a moment after the write."""
    for _ in range(30):
        if await check():
            return
        await anyio.sleep(0.5)
    pytest.fail(f"{what} did not happen within 15s")


def _body(answer: Answer, key: str) -> dict[str, object]:
    record = answer.record()
    body = record.get(key, record)
    assert isinstance(body, dict), f"{answer.call} has no {key}"
    return {str(k): v for k, v in body.items()}


async def _trace(mcp: Live, name: str) -> str:
    trace_id = new_id()
    await mcp.write(
        "trace.create",
        {
            "id": trace_id,
            "name": name,
            "project_name": WRITES_PROJECT,
            "start_time": _now(),
            "input": {"question": "written by the live suite"},
        },
    )
    return trace_id


async def test_a_created_trace_reads_back_with_its_name(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    trace_id = await _trace(mcp, f"{run_prefix}-trace")
    trace = _body(await mcp.read("trace", trace_id), "trace")
    assert trace["name"] == f"{run_prefix}-trace"


async def test_a_trace_created_by_project_id_carries_a_link_to_its_page(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    # By id: a project name is not resolved to an id just to build a link.
    await _trace(mcp, f"{run_prefix}-first")
    project_id = (await mcp.list("project", name=WRITES_PROJECT)).column("id")[0]
    trace_id = new_id()
    result = await mcp.write(
        "trace.create",
        {
            "id": trace_id,
            "name": f"{run_prefix}-linked",
            "project_id": project_id,
            "start_time": _now(),
        },
    )
    url = result.get("url")
    assert isinstance(url, str), f"no link in {result}"
    assert trace_id in url


async def test_an_updated_trace_reads_back_changed(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    trace_id = await _trace(mcp, f"{run_prefix}-before")
    await mcp.write(
        "trace.update",
        {"id": trace_id, "project_name": WRITES_PROJECT, "tags": [f"{run_prefix}-tag"]},
    )
    trace = _body(await mcp.read("trace", trace_id), "trace")
    assert trace["tags"] == [f"{run_prefix}-tag"]


async def test_a_created_span_hangs_under_its_trace(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    trace_id = await _trace(mcp, f"{run_prefix}-parent")
    span_id = new_id()
    await mcp.write(
        "span.create",
        {
            "id": span_id,
            "trace_id": trace_id,
            "name": f"{run_prefix}-span",
            "project_name": WRITES_PROJECT,
            "start_time": _now(),
            "type": "tool",
        },
    )
    spans = (await mcp.read("trace", trace_id)).record()["spans"]
    assert isinstance(spans, list)
    assert [s.get("id") for s in spans if isinstance(s, dict)] == [span_id]


async def test_a_score_lands_on_its_trace(mcp: Live, writable: Manifest, run_prefix: str) -> None:
    trace_id = await _trace(mcp, f"{run_prefix}-scored")
    await mcp.write(
        "score.create",
        {
            "target": "trace",
            "target_id": trace_id,
            "name": "live_suite",
            "value": 0.5,
            "project_name": WRITES_PROJECT,
        },
    )
    scores = _body(await mcp.read("trace", trace_id), "trace").get("feedback_scores")
    assert isinstance(scores, list)
    assert [(s.get("name"), s.get("value")) for s in scores if isinstance(s, dict)] == [
        ("live_suite", 0.5)
    ]


async def test_a_comment_lands_on_its_trace(mcp: Live, writable: Manifest, run_prefix: str) -> None:
    trace_id = await _trace(mcp, f"{run_prefix}-commented")
    await mcp.write(
        "comment.create", {"target": "trace", "target_id": trace_id, "text": run_prefix}
    )
    comments = _body(await mcp.read("trace", trace_id), "trace").get("comments")
    assert isinstance(comments, list)
    assert [c.get("text") for c in comments if isinstance(c, dict)] == [run_prefix]


async def test_a_dataset_and_its_items_are_created(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    name = f"{run_prefix}-dataset"
    await mcp.write("dataset.create", {"name": name})
    await mcp.write(
        "dataset_item.upsert",
        {"dataset_name": name, "items": [{"data": {"q": "one"}}, {"data": {"q": "two"}}]},
    )
    dataset = _body(await mcp.read("dataset", name), "dataset")
    items = await mcp.list("dataset_item", dataset_id=dataset["id"])
    assert sorted(items.column("data.q")) == ["one", "two"]


async def test_an_experiment_and_its_item_are_created(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    dataset = writable.small_dataset
    name = f"{run_prefix}-experiment"
    await mcp.write("experiment.create", {"name": name, "dataset_name": dataset.name})
    experiment = (await mcp.read("experiment", name)).record()
    trace_id = await _trace(mcp, f"{run_prefix}-run")
    await mcp.write(
        "experiment_item.create",
        {
            "experiment_items": [
                {
                    "experiment_id": experiment["id"],
                    "dataset_item_id": dataset.item_ids[0],
                    "trace_id": trace_id,
                }
            ]
        },
    )
    assert (await mcp.read("experiment", name)).record()["trace_count"] == 1


async def test_saved_prompt_versions_read_back_newest_last(
    mcp: Live, writable: Manifest, run_prefix: str
) -> None:
    name = f"{run_prefix}-prompt"
    for template in ("first {{q}}", "second {{q}}"):
        await mcp.write("prompt_version.save", {"name": name, "template": template})
    record = (await mcp.read("prompt", name)).record()
    prompt = record["prompt"]
    assert isinstance(prompt, dict)
    latest = prompt["latest_version"]
    assert isinstance(latest, dict)
    assert (prompt["version_count"], latest["template"]) == (2, "second {{q}}")


async def _thread_status(mcp: Live, m: Manifest) -> object:
    thread = _body(
        await mcp.read("thread", m.lifecycle_thread.id, project_name=m.project_name), "thread"
    )
    return thread["status"]


async def test_closing_a_thread_makes_it_inactive_and_opening_it_active(
    mcp: Live, writable: Manifest
) -> None:
    m = writable
    target = {"thread_id": m.lifecycle_thread.id, "project_name": m.project_name}
    try:
        await mcp.write("thread.close", target)

        async def inactive() -> bool:
            return await _thread_status(mcp, m) == "inactive"

        await _eventually(inactive, "the thread closing")
    finally:
        await mcp.write("thread.open", target)

    async def active() -> bool:
        return await _thread_status(mcp, m) == "active"

    await _eventually(active, "the thread reopening")


@pytest.mark.parametrize("operation", ["resolve", "close"])
async def test_an_issue_leaves_the_open_list_and_reopen_brings_it_back(
    mcp: Live, writable: Manifest, operation: str
) -> None:
    m = writable
    issue = {"issue_id": m.lifecycle_issue.id, "project_name": m.project_name}

    async def open_ids() -> set[str]:
        answer = await mcp.list("agent_insights_issue", project_name=m.project_name)
        return set(answer.column("id")) if answer.total() else set()

    try:
        await mcp.write(f"agent_insights_issue.{operation}", issue)
        assert m.lifecycle_issue.id not in await open_ids()
    finally:
        await mcp.write("agent_insights_issue.reopen", issue)
    assert m.lifecycle_issue.id in await open_ids()

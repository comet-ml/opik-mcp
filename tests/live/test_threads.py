"""Threads, read and listed against the seeded backend."""

from __future__ import annotations

import re

import pytest
from scripts.seed_e2e_backend import Manifest

from tests.live.conftest import Live, continuation

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_a_thread_list_holds_every_seeded_thread(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list("thread", project_name=manifest.project_name, size=100)
    seeded = {t.id for t in manifest.short_threads}
    seeded.add(manifest.long_thread.id)
    assert set(answer.column("id")) == seeded


async def test_a_filter_on_message_count_returns_only_the_long_thread(
    mcp: Live, manifest: Manifest
) -> None:
    answer = await mcp.list(
        "thread", project_name=manifest.project_name, filters="number_of_messages > 100"
    )
    assert answer.column("id") == [manifest.long_thread.id]


async def test_a_short_thread_reads_with_every_turn(mcp: Live, manifest: Manifest) -> None:
    thread = manifest.short_threads[1]
    record = (await mcp.read("thread", thread.id, project_name=manifest.project_name)).record()
    messages = record["messages"]
    assert isinstance(messages, list)
    assert (len(messages), record["messagesTruncated"]) == (thread.turns, False)


async def test_a_long_thread_counts_the_turns_it_did_not_inline(
    mcp: Live, manifest: Manifest
) -> None:
    thread = manifest.long_thread
    record = (await mcp.read("thread", thread.id, project_name=manifest.project_name)).record()
    messages, more = record["messages"], record.get("moreMessages")
    assert isinstance(messages, list)
    assert isinstance(more, str), "a long thread must say it was cut"
    counted = re.search(r"(\d+) of (\d+)", more)
    assert counted, f"the cut states no count: {more}"
    assert (int(counted[1]), int(counted[2])) == (len(messages), thread.turns)
    assert thread.id in more, "the cut must name the call that gets the rest"


async def test_a_scored_thread_carries_its_score(mcp: Live, manifest: Manifest) -> None:
    record = (
        await mcp.read("thread", manifest.scored_thread.id, project_name=manifest.project_name)
    ).record()
    thread = record["thread"]
    assert isinstance(thread, dict)
    scores = thread.get("feedback_scores")
    assert isinstance(scores, list)
    assert manifest.thread_score_name in {s.get("name") for s in scores if isinstance(s, dict)}


async def test_the_call_a_long_thread_names_returns_exactly_the_turns_it_left_out(
    mcp: Live, manifest: Manifest
) -> None:
    thread = manifest.long_thread
    record = (await mcp.read("thread", thread.id, project_name=manifest.project_name)).record()
    messages, more = record["messages"], record.get("moreMessages")
    assert isinstance(messages, list)
    assert isinstance(more, str), "a long thread must say it was cut"
    inlined = {m["trace_id"] for m in messages if isinstance(m, dict)}
    rest = await mcp.list(
        "trace",
        project_name=manifest.project_name,
        filters=f'thread_id = "{thread.id}"',
        **continuation(more),
    )
    shown = set(rest.column("id"))
    assert (shown & inlined, len(inlined) + len(shown)) == (set(), thread.turns)


async def test_every_turn_of_a_thread_carries_its_own_scores(mcp: Live, manifest: Manifest) -> None:
    thread = manifest.short_threads[0]
    record = (await mcp.read("thread", thread.id, project_name=manifest.project_name)).record()
    messages = record["messages"]
    assert isinstance(messages, list)
    scored = [
        {s.get("name") for s in m.get("feedback_scores") or [] if isinstance(s, dict)}
        for m in messages
        if isinstance(m, dict)
    ]
    assert scored == [{"correctness", "hallucination"}] * thread.turns

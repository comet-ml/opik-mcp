"""Datasets, experiments and prompts, read and listed against the seeded backend."""

from __future__ import annotations

import re

import pytest
from scripts.seed_e2e_backend import Manifest

from tests.live.conftest import Live, continuation

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_a_dataset_reads_by_name(mcp: Live, manifest: Manifest) -> None:
    record = (await mcp.read("dataset", manifest.small_dataset.name)).record()
    body = record.get("dataset", record)
    assert isinstance(body, dict)
    assert body["id"] == manifest.small_dataset.id


async def test_a_datasets_items_are_all_listed(mcp: Live, manifest: Manifest) -> None:
    dataset = manifest.small_dataset
    answer = await mcp.list("dataset_item", dataset_id=dataset.id, size=100)
    assert set(answer.column("id")) == set(dataset.item_ids)


@pytest.mark.parametrize("baseline_first", [True, False], ids=["baseline-first", "candidate-first"])
async def test_a_comparison_measures_every_case_against_the_first_experiment(
    mcp: Live, manifest: Manifest, baseline_first: bool
) -> None:
    # Both orders, so one of them never matches the ids' sorted order: a server
    # that reordered them would then compare against the wrong baseline.
    pair = [manifest.baseline.id, manifest.candidate.id]
    answer = await mcp.list(
        "dataset_item",
        dataset_id=manifest.small_dataset.id,
        experiment_ids=pair if baseline_first else pair[::-1],
        size=100,
    )
    moved: set[str] = set()
    for row in answer.rows():
        cell = next(v for k, v in row.items() if k.startswith("correctness"))
        first, second = (float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", cell)[:2])
        # Baseline first, the regressed cases get worse; candidate first, the
        # same cases read as getting better.
        if (second < first) if baseline_first else (second > first):
            moved.add(row["id"])
    assert moved == set(manifest.regressed_item_ids)


async def test_an_experiment_reads_by_name_with_its_run_count(
    mcp: Live, manifest: Manifest
) -> None:
    record = (await mcp.read("experiment", manifest.candidate.name)).record()
    assert (record["id"], record["trace_count"]) == (
        manifest.candidate.id,
        manifest.small_dataset.item_count,
    )


async def test_a_long_prompt_history_counts_the_versions_it_did_not_inline(
    mcp: Live, manifest: Manifest
) -> None:
    prompt = manifest.churn_prompt
    record = (await mcp.read("prompt", prompt.name)).record()
    versions, more = record["versions"], record.get("moreVersions")
    assert isinstance(versions, list)
    assert isinstance(more, str), "a long history must say it was cut"
    counted = re.search(r"(\d+) of (\d+)", more)
    assert counted, f"the cut states no count: {more}"
    assert (int(counted[1]), int(counted[2])) == (len(versions), prompt.version_count)


async def test_the_call_a_long_history_names_returns_exactly_the_versions_it_left_out(
    mcp: Live, manifest: Manifest
) -> None:
    prompt = manifest.churn_prompt
    record = (await mcp.read("prompt", prompt.name)).record()
    body, inlined, more = record["prompt"], record["versions"], record.get("moreVersions")
    assert isinstance(body, dict)
    assert isinstance(inlined, list)
    assert isinstance(more, str), "a long history must say it was cut"
    rest = await mcp.list("prompt_version", prompt_id=body["id"], **continuation(more))
    kept = {v["id"] for v in inlined if isinstance(v, dict)}
    shown = set(rest.column("id"))
    assert (shown & kept, len(kept) + len(shown)) == (set(), prompt.version_count)


async def test_a_name_substring_keeps_the_datasets_that_match_and_only_those(
    mcp: Live, manifest: Manifest
) -> None:
    wide, small = manifest.wide_dataset, manifest.small_dataset
    # Specific enough that a shared workspace's other datasets do not crowd it out.
    ids = set(
        (await mcp.list("dataset", name=wide.name.removeprefix("mcp-"), size=100)).column("id")
    )
    assert (wide.id in ids, small.id in ids) == (True, False)


async def test_a_name_substring_keeps_the_experiments_that_match_and_only_those(
    mcp: Live, manifest: Manifest
) -> None:
    name = manifest.candidate.name.removeprefix("mcp-")
    ids = set((await mcp.list("experiment", name=name, size=100)).column("id"))
    assert (manifest.candidate.id in ids, manifest.baseline.id in ids) == (True, False)


async def test_a_wide_dataset_names_the_columns_it_leaves_out(
    mcp: Live, manifest: Manifest
) -> None:
    dataset = manifest.wide_dataset
    answer = await mcp.list("dataset_item", dataset_id=dataset.id)
    shown = {c.removeprefix("data.") for c in answer.rows()[0] if c.startswith("data.")}
    note = next((line for line in answer.text.splitlines() if "omitted:" in line), "")
    named = {c.strip(" .") for c in note.split("omitted:", 1)[-1].split(",")} if note else set()
    assert answer.total() == dataset.item_count
    # Every column is either a column of the table or named as left out.
    assert (shown & named, shown | named) == (set(), set(dataset.columns))


async def test_a_prompt_inlines_its_versions_newest_first(mcp: Live, manifest: Manifest) -> None:
    prompt = manifest.answer_prompt
    versions = (await mcp.read("prompt", prompt.name)).record()["versions"]
    assert isinstance(versions, list)
    first = versions[0]
    assert isinstance(first, dict)
    assert (len(versions), first["template"]) == (prompt.version_count, prompt.latest_template)


async def test_the_versions_past_the_inline_cut_are_the_oldest(
    mcp: Live, manifest: Manifest
) -> None:
    prompt = manifest.churn_prompt
    record = (await mcp.read("prompt", prompt.name)).record()
    body, inlined, more = record["prompt"], record["versions"], record.get("moreVersions")
    assert isinstance(body, dict)
    assert isinstance(inlined, list)
    assert isinstance(more, str), "a long history must say it was cut"
    rest = await mcp.list("prompt_version", prompt_id=body["id"], **continuation(more))

    def revision(template: object) -> int:
        found = re.search(r"Revision (\d+)", str(template))
        assert found, f"not a seeded revision: {template}"
        return int(found[1])

    kept = [revision(v.get("template")) for v in inlined if isinstance(v, dict)]
    later = [revision(t) for t in rest.column("template")]
    assert max(later) < min(kept), "the cut must leave out the oldest versions, not any"

"""Datasets, experiments and prompts, read and listed against the seeded backend."""

from __future__ import annotations

import re

import pytest
from scripts.seed_e2e_backend import PREFIX, Manifest

from tests.live.conftest import Live, continuation

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_a_dataset_is_found_by_a_name_substring(mcp: Live, manifest: Manifest) -> None:
    dataset = manifest.wide_dataset
    answer = await mcp.list("dataset", name=dataset.name.removeprefix(PREFIX))
    assert dataset.id in answer.column("id")


async def test_a_dataset_reads_by_name(mcp: Live, manifest: Manifest) -> None:
    record = (await mcp.read("dataset", manifest.small_dataset.name)).record()
    body = record.get("dataset", record)
    assert isinstance(body, dict)
    assert body["id"] == manifest.small_dataset.id


async def test_a_datasets_items_are_all_listed(mcp: Live, manifest: Manifest) -> None:
    dataset = manifest.small_dataset
    answer = await mcp.list("dataset_item", dataset_id=dataset.id, size=100)
    assert set(answer.column("id")) == set(dataset.item_ids)


async def test_a_wide_dataset_names_every_column_it_does_not_show(
    mcp: Live, manifest: Manifest
) -> None:
    dataset = manifest.wide_dataset
    answer = await mcp.list("dataset_item", dataset_id=dataset.id)
    unnamed = [column for column in dataset.columns if column not in answer.text]
    assert answer.total() == dataset.item_count
    assert not unnamed, f"columns cut without a word: {unnamed}"


async def test_a_comparison_finds_exactly_the_regressed_cases(
    mcp: Live, manifest: Manifest
) -> None:
    answer = await mcp.list(
        "dataset_item",
        dataset_id=manifest.small_dataset.id,
        experiment_ids=[manifest.baseline.id, manifest.candidate.id],
        size=100,
    )
    regressed: set[str] = set()
    for row in answer.rows():
        cell = next(v for k, v in row.items() if k.startswith("correctness"))
        before, after = (float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", cell)[:2])
        if after < before:
            regressed.add(row["id"])
    assert regressed == set(manifest.regressed_item_ids)


async def test_an_experiment_is_found_by_a_name_substring(mcp: Live, manifest: Manifest) -> None:
    experiment = manifest.candidate
    answer = await mcp.list("experiment", name=experiment.name.removeprefix(PREFIX))
    assert experiment.id in answer.column("id")


async def test_an_experiment_reads_by_name_with_its_run_count(
    mcp: Live, manifest: Manifest
) -> None:
    record = (await mcp.read("experiment", manifest.candidate.name)).record()
    assert (record["id"], record["trace_count"]) == (
        manifest.candidate.id,
        manifest.small_dataset.item_count,
    )


async def test_a_prompt_reads_with_every_version_and_the_latest_template(
    mcp: Live, manifest: Manifest
) -> None:
    prompt = manifest.answer_prompt
    record = (await mcp.read("prompt", prompt.name)).record()
    body, versions = record["prompt"], record["versions"]
    assert isinstance(body, dict)
    assert isinstance(versions, list)
    latest = body["latest_version"]
    assert isinstance(latest, dict)
    assert (len(versions), latest["template"]) == (prompt.version_count, prompt.latest_template)


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

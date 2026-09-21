"""Every claim an entity description makes, asserted against the stub backend.

WHAT THIS COVERS THAT NOTHING ELSE DOES. ``EntityHandler.description`` was
asserted nowhere. Its own docstring records that two of them contradicted the
code before anyone noticed — the ``test_suite_item`` entity advertised that
the parent read inlined up to 200 items, and it inlined none — precisely
because no output ever showed them and no test ever read them. A description
is a promise made to whoever reads the registry next, and an unchecked promise
drifts silently.

So this is a table of ``(entity, claim phrase, probe)``. Each phrase is a
verbatim substring of the description it belongs to; each probe drives the
real server over stdio against ``stub_backend`` and asserts the thing the
phrase says. The claim table is the documentation; the probes are what make it
true.

The **coverage guard** is the half that keeps it honest. It runs in
``make check`` (no marker, no subprocess, no backend): it splits every
description into sentences and fails if a sentence has no claim phrase in it.
Adding a sentence to a description therefore fails the build until it is
probed — which is the property this ticket exists to buy. Deleting a probe
fails it too, and a phrase that is not verbatim in its description fails it
loudest of all, because a claim table that has drifted from the descriptions
is worse than none.

Scope: the registry's own descriptions. The tool descriptions in ``server.py``
are already pinned byte-for-byte by ``tests/conformance/test_schema_snapshots``
and measured by the surface budget, so what they need is a reviewer, not a
probe; where one of them makes an entity-specific promise it is the entity's
description that states it, and that is the line this table asserts.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from opik_mcp.read_list.registry import ENTITY_REGISTRY
from tests.e2e.stub_backend import (
    EXPERIMENT_A,
    EXPERIMENT_B,
    ISSUE_ID,
    OTHER_SUITE_ID,
    PROJECT_ID,
    PROJECT_NAME,
    PROMPT_ID,
    PROMPT_NAME,
    SPAN_ID,
    SUITE_ID,
    SUITE_NAME,
    THREAD_ID,
    TRACE_ID,
    StubBackend,
)

_TIMEOUT_S = 90


# --- driving one session --------------------------------------------------- #


@dataclass
class Driver:
    """One live session, plus the stub it is pointed at.

    A probe may bend the stub mid-session — grow the span tree past the
    inline limit, take the Diagnostics job away — because the stub answers
    over HTTP from this process while the server runs in another. That is the
    only way to probe a claim about a boundary ("up to 200") without shipping
    a second fixture for every threshold.
    """

    session: ClientSession
    backend: StubBackend

    async def call(self, tool: str, **args: Any) -> str:
        result = await self.session.call_tool(tool, args)
        text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
        assert not result.isError, f"{tool}({args}) was refused: {text}"
        return text

    async def refuse(self, tool: str, **args: Any) -> str:
        result = await self.session.call_tool(tool, args)
        text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
        assert result.isError, f"{tool}({args}) was answered, expected a refusal: {text}"
        return text

    async def read_json(self, **args: Any) -> dict[str, Any]:
        """A ``read``'s payload, past the token-count header line it carries."""
        text = await self.call("read", **args)
        body = text.split("\n", 1)[1] if text.startswith("[read:") else text
        parsed = json.loads(body)
        assert isinstance(parsed, dict)
        return parsed


ProbeFn = Callable[[Driver], Awaitable[None]]

PROBES: dict[str, ProbeFn] = {}


def probe(name: str) -> Callable[[ProbeFn], ProbeFn]:
    """Register a probe under the name the claim table refers to it by."""

    def register(fn: ProbeFn) -> ProbeFn:
        assert name not in PROBES, f"two probes named {name!r}"
        PROBES[name] = fn
        return fn

    return register


# --- the claim table ------------------------------------------------------- #


@dataclass(frozen=True)
class Claim:
    """One promise a description makes, and what makes it true.

    ``phrase`` must appear verbatim in ``ENTITY_REGISTRY[entity].description``.
    That is deliberate duplication: it is what lets the coverage guard tie a
    probe to the words it is answering for, and what makes a reworded
    description fail loudly instead of quietly losing its probe.
    """

    entity: str
    phrase: str
    probe: str


CLAIMS: tuple[Claim, ...] = (
    # -- project ------------------------------------------------------------ #
    Claim("project", "the record", "project_record"),
    Claim(
        "project",
        "the last 7 days of SDK traffic against the 7 before",
        "project_window_and_its_predecessor",
    ),
    Claim("project", "the names it records", "project_vocabulary"),
    Claim("project", "what is freshest in it", "project_freshest"),
    # -- trace --------------------------------------------------------------- #
    Claim("trace", "up to 200 spans inlined", "trace_inlines_at_most_200_spans"),
    Claim("trace", "bodies slim", "trace_asks_for_slim_spans"),
    Claim("trace", "Returns {trace, spans, spansTruncated}", "trace_returns_its_three_keys"),
    Claim(
        "trace",
        "spanBodies saying what the cut took when any span was inlined",
        "trace_counts_the_spans_the_cut_took",
    ),
    Claim(
        "trace",
        "moreSpans with the call for the rest when the tree is longer than 200",
        "trace_hands_over_the_call_for_the_rest",
    ),
    # -- span ---------------------------------------------------------------- #
    Claim(
        "span",
        "Single span: inputs, outputs, metadata, timing, feedback_scores",
        "span_read_carries_its_fields",
    ),
    Claim(
        "span",
        "list('span', project_id=…, filters=…) searches spans across a project",
        "span_list_searches_a_project",
    ),
    # -- thread -------------------------------------------------------------- #
    Claim("thread", "metadata + messages list", "thread_returns_metadata_and_turns"),
    Claim("thread", "up to 200 inlined", "thread_inlines_at_most_200_turns"),
    Claim(
        "thread",
        "Returns {thread, messages, messagesTruncated}",
        "thread_returns_its_three_keys",
    ),
    Claim(
        "thread",
        "messageBodies saying what the cut took when any turn was inlined",
        "thread_counts_the_turns_the_cut_took",
    ),
    Claim(
        "thread",
        "moreMessages with the call for the rest past 200",
        "thread_hands_over_the_call_for_the_rest",
    ),
    Claim(
        "thread",
        "Requires project scope — pass a thread link/URI or project_id",
        "thread_read_requires_project_scope",
    ),
    Claim(
        "thread",
        "list('thread', project_id=…) enumerates a project's threads",
        "thread_list_enumerates_a_project",
    ),
    # -- dataset ------------------------------------------------------------- #
    Claim("dataset", "Opik dataset (REST /datasets/{id})", "dataset_read_hits_the_rest_route"),
    Claim(
        "dataset",
        "A test suite is a dataset whose experiments carry "
        "evaluation_method = evaluation_suite; it is the same record",
        "dataset_and_test_suite_are_one_record",
    ),
    # -- dataset_item --------------------------------------------------------- #
    Claim("dataset_item", "Dataset item", "dataset_item_lists_a_suites_cases"),
    Claim(
        "dataset_item",
        "there is no read of an item, and read('dataset') returns the dataset "
        "record without its items",
        "dataset_item_has_no_read_and_the_parent_inlines_none",
    ),
    Claim(
        "dataset_item",
        "Columns are the items' data keys, discovered from each page",
        "dataset_item_columns_come_from_the_page",
    ),
    Claim(
        "dataset_item",
        "With experiment_ids the same list compares those experiments case by case",
        "dataset_item_compares_two_experiments",
    ),
    # -- experiment ----------------------------------------------------------- #
    Claim("experiment", "Experiment status + summary scores", "experiment_status_and_scores"),
    # -- prompt --------------------------------------------------------------- #
    Claim("prompt", "full version list (up to 100 inlined)", "prompt_inlines_at_most_100"),
    Claim(
        "prompt",
        "Returns {prompt, versions, versionsTruncated}, plus moreVersions with "
        "the call for the rest past 100",
        "prompt_hands_over_the_call_for_the_rest",
    ),
    # -- prompt_version -------------------------------------------------------- #
    Claim("prompt_version", "Prompt version", "prompt_version_rows_are_versions"),
    Claim(
        "prompt_version",
        "Currently list-only — pass prompt_id to enumerate",
        "prompt_version_is_list_only",
    ),
    Claim(
        "prompt_version",
        "Use read('prompt', id) to get the prompt + all versions in one call",
        "prompt_version_parent_read_has_them_all",
    ),
    # -- project_metric --------------------------------------------------------- #
    Claim(
        "project_metric",
        "One project metric over time, as a table of buckets",
        "project_metric_is_a_table_of_buckets",
    ),
    Claim(
        "project_metric",
        "Reference: schema('list.project_metric')",
        "project_metric_reference_answers",
    ),
    # -- score_name --------------------------------------------------------------- #
    Claim(
        "score_name",
        "A feedback score name recorded in a project — what a score filter or a "
        "grouped score chart is named with",
        "score_name_names_a_filterable_score",
    ),
    Claim(
        "score_name",
        "Trace, span and thread names come back together",
        "score_name_does_not_say_which_kind",
    ),
    Claim(
        "score_name",
        "the endpoint has no paging of its own, so pages are cut here",
        "score_name_pages_are_cut_here",
    ),
    # -- online_rule ---------------------------------------------------------------- #
    Claim(
        "online_rule",
        "An automation rule evaluator on a project — what scores the traces as "
        "they arrive, and so where most of its score names come from",
        "online_rule_is_where_score_names_come_from",
    ),
    # -- agent_insights_issue --------------------------------------------------------- #
    Claim(
        "agent_insights_issue",
        "a recurring failure the Diagnostics job grouped across a project's "
        "traces, with severity, status, occurrence counts, cause and suggested fix",
        "issue_record_carries_its_fields",
    ),
    Claim(
        "agent_insights_issue",
        "returns open issues ranked as the Diagnostics page ranks them "
        "(most recently seen first); pass status='resolved' or 'closed' for the rest",
        "issue_list_defaults_to_open",
    ),
    Claim(
        "agent_insights_issue",
        "returns {issue, example_trace_ids, details, url, trace_url_template}",
        "issue_read_returns_its_five_keys",
    ),
    Claim(
        "agent_insights_issue",
        "the deduped ids of traces that exhibit it",
        "issue_trace_ids_are_deduped",
    ),
    Claim(
        "agent_insights_issue",
        "UI links to hand the user (omitted when the Opik URL or workspace is unknown)",
        "issue_links_are_omitted_without_a_url",
    ),
    Claim(
        "agent_insights_issue",
        "Counts are all-time unless since/until narrow the window (truncated to UTC report days)",
        "issue_counts_are_all_time_unless_windowed",
    ),
)


# --- the probes ------------------------------------------------------------- #


@probe("project_record")
async def _project_record(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="project", id=PROJECT_NAME)
    assert payload["project"]["id"] == PROJECT_ID
    assert payload["project"]["name"] == PROJECT_NAME


@probe("project_window_and_its_predecessor")
async def _project_window(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="project", id=PROJECT_NAME)
    window = payload["summary"]["window"]
    assert window["days"] == 7, "the default summary period is the last 7 days"
    assert window["compared_to"], "and it is reported against the 7 before it"
    traces = payload["summary"]["traces"]
    assert traces["count"] == {"current": 120.0, "previous": 80.0}
    # "SDK traffic", not all of it: the summary declares the source it counted.
    assert payload["summary"]["source"] == "sdk"


@probe("project_vocabulary")
async def _project_vocabulary(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="project", id=PROJECT_NAME)
    vocabulary = payload["vocabulary"]
    assert vocabulary["score_names"]["names"] == ["Hallucination", "Answer Relevance"]
    assert "total_tokens" in vocabulary["usage_keys"]["names"]
    assert vocabulary["online_rules"]["names"] == ["hallucination-judge"]


@probe("project_freshest")
async def _project_freshest(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="project", id=PROJECT_NAME)
    assert payload["contains"]["experiment"]["name"] == "rerank-v3"


@probe("trace_inlines_at_most_200_spans")
async def _trace_inline_limit(drive: Driver) -> None:
    drive.backend.span_count = 250
    payload = await drive.read_json(entity_type="trace", id=TRACE_ID)
    assert len(payload["spans"]) == 200, "the tree is inlined up to 200 spans and no further"
    assert payload["spansTruncated"] is True
    # And a tree inside the limit is not flagged as cut.
    drive.backend.span_count = 3
    payload = await drive.read_json(entity_type="trace", id=TRACE_ID)
    assert len(payload["spans"]) == 3
    assert payload["spansTruncated"] is False


@probe("trace_asks_for_slim_spans")
async def _trace_slim_spans(drive: Driver) -> None:
    drive.backend.requests.clear()
    await drive.read_json(entity_type="trace", id=TRACE_ID)
    spans_call = next(r for r in drive.backend.sent("/v1/private/spans") if r.method == "GET")
    assert spans_call.query.get("truncate") == ["true"], (
        "the children are asked for slim; the trace itself is not"
    )
    assert not drive.backend.one(f"/v1/private/traces/{TRACE_ID}").query.get("truncate")


@probe("trace_returns_its_three_keys")
async def _trace_keys(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="trace", id=TRACE_ID)
    assert {"trace", "spans", "spansTruncated"} <= payload.keys()


@probe("trace_counts_the_spans_the_cut_took")
async def _trace_span_bodies(drive: Driver) -> None:
    drive.backend.span_count = 4
    payload = await drive.read_json(entity_type="trace", id=TRACE_ID)
    # One of the four spans came back with a cut body; the notice says so
    # rather than leaving the caller to compare lengths themselves.
    assert "1 of 4" in payload["spanBodies"]
    assert "read('span', id)" in payload["spanBodies"]


@probe("trace_hands_over_the_call_for_the_rest")
async def _trace_more_spans(drive: Driver) -> None:
    drive.backend.span_count = 250
    payload = await drive.read_json(entity_type="trace", id=TRACE_ID)
    more = payload["moreSpans"]
    assert "list('span'" in more
    assert TRACE_ID in more, "the call has to name the trace, or it is not the rest of THIS tree"
    # It carries where to resume from — a flag saying "there is more" that
    # the caller cannot act on is the defect this field exists to fix.
    assert "page=" in more and "size=" in more
    assert "250" in more, "and how much was left out"
    # Inside the limit there is nothing left over, so nothing is offered.
    drive.backend.span_count = 3
    assert "moreSpans" not in await drive.read_json(entity_type="trace", id=TRACE_ID)


@probe("span_read_carries_its_fields")
async def _span_fields(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="span", id=SPAN_ID)
    assert payload["id"] == SPAN_ID
    assert payload["input"] == {"amount": 12}
    assert payload["output"]
    assert payload["start_time"] and payload["end_time"]


@probe("span_list_searches_a_project")
async def _span_list(drive: Driver) -> None:
    answer = await drive.call(
        "list",
        entity_type="span",
        project_id=PROJECT_ID,
        filters=f'trace_id = "{TRACE_ID}"',
    )
    assert SPAN_ID in answer
    sent = drive.backend.sent("/v1/private/spans")[-1]
    assert sent.query.get("project_id") == [PROJECT_ID]


@probe("thread_returns_metadata_and_turns")
async def _thread_metadata_and_turns(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)
    assert payload["thread"]["thread_id"] == THREAD_ID
    assert payload["messages"], "a thread with turns comes back with them"
    # Conversation order, not the order the backend happened to send.
    times = [m["start_time"] for m in payload["messages"]]
    assert times == sorted(times)


@probe("thread_inlines_at_most_200_turns")
async def _thread_inline_limit(drive: Driver) -> None:
    drive.backend.thread_turn_count = 250
    payload = await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)
    assert len(payload["messages"]) == 200
    assert payload["messagesTruncated"] is True
    drive.backend.thread_turn_count = 2
    payload = await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)
    assert len(payload["messages"]) == 2
    assert payload["messagesTruncated"] is False


@probe("thread_returns_its_three_keys")
async def _thread_keys(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)
    assert {"thread", "messages", "messagesTruncated"} <= payload.keys()


@probe("thread_counts_the_turns_the_cut_took")
async def _thread_message_bodies(drive: Driver) -> None:
    drive.backend.thread_turn_count = 4
    payload = await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)
    assert "1 of 4 turns" in payload["messageBodies"]
    assert "read('trace', trace_id)" in payload["messageBodies"], (
        "and the call that fetches the whole turn back"
    )


@probe("thread_hands_over_the_call_for_the_rest")
async def _thread_more_messages(drive: Driver) -> None:
    drive.backend.thread_turn_count = 250
    payload = await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)
    assert "list('trace'" in payload["moreMessages"]
    assert THREAD_ID in payload["moreMessages"]
    drive.backend.thread_turn_count = 2
    assert "moreMessages" not in await drive.read_json(
        entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME
    )


@probe("thread_read_requires_project_scope")
async def _thread_needs_project(drive: Driver) -> None:
    refusal = await drive.refuse("read", entity_type="thread", id=THREAD_ID)
    assert "project" in refusal.lower()
    # Either scope satisfies it, and so does the link that carries one.
    await drive.read_json(entity_type="thread", id=THREAD_ID, project_id=PROJECT_ID)
    await drive.read_json(entity_type="thread", id=THREAD_ID, project_name=PROJECT_NAME)


@probe("thread_list_enumerates_a_project")
async def _thread_list(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="thread", project_id=PROJECT_ID)
    assert THREAD_ID in answer
    sent = drive.backend.sent("/v1/private/traces/threads")[-1]
    assert sent.query.get("project_id") == [PROJECT_ID]


@probe("dataset_read_hits_the_rest_route")
async def _dataset_route(drive: Driver) -> None:
    drive.backend.requests.clear()
    payload = await drive.read_json(entity_type="dataset", id=SUITE_ID)
    assert payload["id"] == SUITE_ID
    assert drive.backend.called(f"/v1/private/datasets/{SUITE_ID}")


@probe("dataset_and_test_suite_are_one_record")
async def _dataset_is_a_test_suite(drive: Driver) -> None:
    suite = await drive.read_json(entity_type="dataset", id=SUITE_ID)
    # The alias resolves to the same entity and the same record — "it is the
    # same record" is the claim, so the two reads have to agree field for field.
    aliased = await drive.read_json(entity_type="test_suite", id=SUITE_ID)
    assert aliased == suite
    experiment = await drive.read_json(entity_type="experiment", id=EXPERIMENT_A)
    assert experiment["dataset_id"] == SUITE_ID
    assert experiment["evaluation_method"] == "evaluation_suite"


@probe("dataset_item_lists_a_suites_cases")
async def _dataset_item_list(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="dataset_item", dataset_id=SUITE_ID)
    assert "Found 3 dataset_items" in answer
    assert "what is the capital of France?" in answer


@probe("dataset_item_has_no_read_and_the_parent_inlines_none")
async def _dataset_item_no_read(drive: Driver) -> None:
    refusal = await drive.refuse("read", entity_type="dataset_item", id=SUITE_ID)
    assert "dataset_item" in refusal
    parent = await drive.read_json(entity_type="dataset", id=SUITE_ID)
    assert "items" not in parent, "the parent read is the record, not the cases"
    # dataset_id is what enumerates them, so without it the call cannot run.
    await drive.refuse("list", entity_type="dataset_item")


@probe("dataset_item_columns_come_from_the_page")
async def _dataset_item_columns(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="dataset_item", dataset_id=SUITE_ID)
    assert "data.question" in answer and "data.expected_answer" in answer
    # And the page says where the columns came from, so a cut is never silent.
    assert "the items' data keys" in answer


@probe("dataset_item_compares_two_experiments")
async def _dataset_item_compare(drive: Driver) -> None:
    answer = await drive.call(
        "list", entity_type="dataset_item", experiment_ids=[EXPERIMENT_A, EXPERIMENT_B]
    )
    assert EXPERIMENT_A in answer and EXPERIMENT_B in answer
    # The dataset is resolved from the runs, not asked for.
    assert drive.backend.called(f"/v1/private/datasets/{SUITE_ID}/items/experiments/items")


@probe("experiment_status_and_scores")
async def _experiment_summary(drive: Driver) -> None:
    payload = await drive.read_json(entity_type="experiment", id=EXPERIMENT_A)
    assert payload["status"] == "completed"
    names = {score["name"] for score in payload["feedback_scores"]}
    assert names == {"correctness", "hallucination"}


@probe("prompt_inlines_at_most_100")
async def _prompt_inline_limit(drive: Driver) -> None:
    drive.backend.prompt_version_count = 130
    payload = await drive.read_json(entity_type="prompt", id=PROMPT_ID)
    assert payload["prompt"]["name"] == PROMPT_NAME
    assert len(payload["versions"]) == 100
    assert payload["versionsTruncated"] is True
    drive.backend.prompt_version_count = 3
    payload = await drive.read_json(entity_type="prompt", id=PROMPT_ID)
    assert len(payload["versions"]) == 3
    assert payload["versionsTruncated"] is False


@probe("prompt_hands_over_the_call_for_the_rest")
async def _prompt_more_versions(drive: Driver) -> None:
    drive.backend.prompt_version_count = 130
    payload = await drive.read_json(entity_type="prompt", id=PROMPT_ID)
    assert {"prompt", "versions", "versionsTruncated"} <= payload.keys()
    assert "list('prompt_version'" in payload["moreVersions"]
    assert PROMPT_ID in payload["moreVersions"]
    drive.backend.prompt_version_count = 3
    assert "moreVersions" not in await drive.read_json(entity_type="prompt", id=PROMPT_ID)


@probe("prompt_version_rows_are_versions")
async def _prompt_version_rows(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="prompt_version", prompt_id=PROMPT_ID)
    assert "Answer the refund question, revision 1." in answer


@probe("prompt_version_is_list_only")
async def _prompt_version_list_only(drive: Driver) -> None:
    refusal = await drive.refuse("read", entity_type="prompt_version", id=PROMPT_ID)
    assert "prompt_version" in refusal
    # prompt_id is the parent that enumerates them.
    await drive.refuse("list", entity_type="prompt_version")
    await drive.call("list", entity_type="prompt_version", prompt_id=PROMPT_ID)


@probe("prompt_version_parent_read_has_them_all")
async def _prompt_version_via_parent(drive: Driver) -> None:
    drive.backend.prompt_version_count = 3
    payload = await drive.read_json(entity_type="prompt", id=PROMPT_ID)
    commits = [version["commit"] for version in payload["versions"]]
    assert commits == ["v1", "v2", "v3"], "one call, the prompt and every version"


@probe("project_metric_is_a_table_of_buckets")
async def _project_metric_table(drive: Driver) -> None:
    answer = await drive.call(
        "list",
        entity_type="project_metric",
        project_name=PROJECT_NAME,
        metric_type="trace_count",
    )
    assert "time | traces" in answer
    assert "2026-09-01 | 0" in answer and "2026-09-02 | 4" in answer
    # A series is not a collection, so it carries no page/total header.
    assert "Found " not in answer


@probe("project_metric_reference_answers")
async def _project_metric_reference(drive: Driver) -> None:
    answer = await drive.call("schema", operation="list.project_metric")
    reference = json.loads(answer)
    assert reference["entity"] == "project_metric"
    assert "trace_count" in reference["metric_types"]


@probe("score_name_names_a_filterable_score")
async def _score_name_rows(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="score_name", project_name=PROJECT_NAME)
    assert "Hallucination" in answer and "Answer Relevance" in answer
    # The name is what a filter is written with, and the filter compiles.
    await drive.call(
        "list",
        entity_type="trace",
        project_name=PROJECT_NAME,
        filters="feedback_scores.Hallucination < 0.5",
    )


@probe("score_name_does_not_say_which_kind")
async def _score_name_kind(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="score_name", project_name=PROJECT_NAME)
    assert "does not separate them" in answer, (
        "the rows cannot carry this, so the page has to say it under them"
    )


@probe("score_name_pages_are_cut_here")
async def _score_name_paging(drive: Driver) -> None:
    drive.backend.score_names = [f"score-{index}" for index in range(5)]
    answer = await drive.call("list", entity_type="score_name", project_name=PROJECT_NAME, size=2)
    assert "showing 2 of 5" in answer
    # The endpoint takes no paging, so the whole list is fetched and cut here.
    sent = drive.backend.sent("/v1/private/projects/feedback-scores/names")[-1]
    assert "page" not in sent.query and "size" not in sent.query


@probe("online_rule_is_where_score_names_come_from")
async def _online_rule_rows(drive: Driver) -> None:
    answer = await drive.call("list", entity_type="online_rule", project_name=PROJECT_NAME)
    assert "hallucination-judge" in answer
    assert "llm_as_judge" in answer
    # The project overview reads the same rules as the source of its names.
    overview = await drive.read_json(entity_type="project", id=PROJECT_NAME)
    assert overview["vocabulary"]["online_rules"]["names"] == ["hallucination-judge"]


@probe("issue_record_carries_its_fields")
async def _issue_fields(drive: Driver) -> None:
    payload = await drive.read_json(
        entity_type="agent_insights_issue", id=ISSUE_ID, project_name=PROJECT_NAME
    )
    issue = payload["issue"]
    assert issue["severity"] == "high"
    assert issue["status"] == "open"
    assert issue["total_occurrences"] == 42
    assert issue["cause"] and issue["suggested_fix"]


@probe("issue_list_defaults_to_open")
async def _issue_list_status(drive: Driver) -> None:
    from tests.e2e.stub_backend import _issue

    closed = {**_issue(), "id": ISSUE_ID.replace("50", "51"), "status": "closed"}
    drive.backend.issues = [_issue(), closed]

    default = await drive.call(
        "list", entity_type="agent_insights_issue", project_name=PROJECT_NAME
    )
    assert ISSUE_ID in default and closed["id"] not in default
    sent = drive.backend.sent("/v1/private/agent-insights/issues")[-1]
    assert sent.query.get("status") in (None, ["open"])

    rest = await drive.call(
        "list", entity_type="agent_insights_issue", project_name=PROJECT_NAME, status="closed"
    )
    assert closed["id"] in rest


@probe("issue_read_returns_its_five_keys")
async def _issue_read_keys(drive: Driver) -> None:
    payload = await drive.read_json(
        entity_type="agent_insights_issue", id=ISSUE_ID, project_name=PROJECT_NAME
    )
    assert {
        "issue",
        "example_trace_ids",
        "details",
        "url",
        "trace_url_template",
    } <= payload.keys()
    assert "{trace_id}" in payload["trace_url_template"]
    assert payload["details"][0]["report_date"] == "2026-09-08"


@probe("issue_trace_ids_are_deduped")
async def _issue_trace_ids(drive: Driver) -> None:
    payload = await drive.read_json(
        entity_type="agent_insights_issue", id=ISSUE_ID, project_name=PROJECT_NAME
    )
    ids = payload["example_trace_ids"]
    # The stub's two report days name the same trace twice, across three
    # mentions; the read is what turns that into two ids.
    mentions = [
        trace_id for day in payload["details"] for trace_id in day["metadata"]["example_trace_ids"]
    ]
    assert len(mentions) == 3
    assert ids == list(dict.fromkeys(mentions))
    assert TRACE_ID in ids


@probe("issue_links_are_omitted_without_a_url")
async def _issue_links(drive: Driver) -> None:
    payload = await drive.read_json(
        entity_type="agent_insights_issue", id=ISSUE_ID, project_name=PROJECT_NAME
    )
    assert f"/projects/{PROJECT_ID}/diagnostics?issue={ISSUE_ID}" in payload["url"]
    # The "omitted when …" half is a unit-level claim about an unconfigured
    # install: this session has a URL and a workspace by construction, so
    # what the e2e stub can show is that a configured one carries both links.
    assert payload["trace_url_template"].startswith("http")


@probe("issue_counts_are_all_time_unless_windowed")
async def _issue_window(drive: Driver) -> None:
    drive.backend.requests.clear()
    await drive.read_json(
        entity_type="agent_insights_issue", id=ISSUE_ID, project_name=PROJECT_NAME
    )
    unwindowed = drive.backend.sent(f"/v1/private/agent-insights/issues/{ISSUE_ID}")[-1]
    assert "from_date" not in unwindowed.query, "no window means all-time, not a default one"

    await drive.read_json(
        entity_type="agent_insights_issue",
        id=ISSUE_ID,
        project_name=PROJECT_NAME,
        since="2026-09-08T13:45:00Z",
        until="2026-09-09T13:45:00Z",
    )
    windowed = drive.backend.sent(f"/v1/private/agent-insights/issues/{ISSUE_ID}")[-1]
    # Truncated to UTC report days: an instant sent to a day-keyed endpoint
    # silently drops rows, which is the defect the truncation exists to stop.
    assert windowed.query["from_date"] == ["2026-09-08"]
    assert windowed.query["to_date"] == ["2026-09-09"]


# --- the coverage guard (runs in `make check`) ------------------------------- #

#: A sentence ends at ``.``/``?``/``!`` followed by something that starts a new
#: one. A lowercase ``list(``/``read(`` does too — the descriptions write a
#: whole claim as a call, and treating that as a continuation would let it ride
#: into the previous sentence unprobed.
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+(?=[A-Z(`'\"]|list\(|read\(|write\(|schema\()")


def sentences(description: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(description.strip()) if part.strip()]


def test_every_claim_phrase_is_verbatim_in_its_description() -> None:
    """The table is only a claim table if the claims are the description's own
    words. A phrase that has drifted still passes its probe and no longer says
    anything about what the registry promises — the worst of both."""
    missing = [
        (claim.entity, claim.phrase)
        for claim in CLAIMS
        if claim.phrase not in ENTITY_REGISTRY[claim.entity].description
    ]
    assert not missing, (
        "these claim phrases are not verbatim in their entity's description "
        f"(reword the claim, not the assertion): {missing}"
    )


def test_every_claim_names_a_probe_that_exists() -> None:
    orphans = sorted({claim.probe for claim in CLAIMS} - PROBES.keys())
    assert not orphans, f"claims naming no probe: {orphans}"


def test_no_probe_is_unreferenced() -> None:
    """A probe nothing claims is a test that proves something about the server
    and nothing about the description, which is what this file is for."""
    unused = sorted(PROBES.keys() - {claim.probe for claim in CLAIMS})
    assert not unused, f"probes no claim refers to: {unused}"


def test_every_entity_in_the_registry_is_in_the_claim_table() -> None:
    covered = {claim.entity for claim in CLAIMS}
    assert covered == set(ENTITY_REGISTRY), (
        "every readable/listable entity describes itself, so every one of them "
        f"owes a claim: missing={sorted(set(ENTITY_REGISTRY) - covered)} "
        f"unknown={sorted(covered - set(ENTITY_REGISTRY))}"
    )


def test_every_description_sentence_has_a_probe() -> None:
    """THE GUARD. Add a sentence to an entity description and this fails until
    a claim covers it and a probe answers for it.

    A sentence is covered when some claim phrase for that entity lies inside
    it — or, for a claim written across a sentence boundary, when the phrase
    contains it. Both directions, because the descriptions are prose and a
    claim is sometimes the better unit and sometimes the worse one.
    """
    unprobed: list[str] = []
    for entity, handler in ENTITY_REGISTRY.items():
        phrases = [claim.phrase for claim in CLAIMS if claim.entity == entity]
        for sentence in sentences(handler.description):
            if not any(phrase in sentence or sentence in phrase for phrase in phrases):
                unprobed.append(f"{entity}: {sentence}")
    assert not unprobed, (
        "these description sentences claim something no probe asserts. Add a "
        "Claim naming a probe that drives the stub, or delete the sentence:\n  "
        + "\n  ".join(unprobed)
    )


def test_the_guard_notices_an_unprobed_sentence() -> None:
    """The guard is only worth having if it can fail. A guard that silently
    passes on an empty claim list is the shape of bug it exists to catch."""
    assert sentences("One thing. And another. list('x', y=1) does a third.") == [
        "One thing.",
        "And another.",
        "list('x', y=1) does a third.",
    ]
    description = ENTITY_REGISTRY["trace"].description
    assert len(sentences(description)) >= 2
    assert not any("nothing claims this" in sentence for sentence in sentences(description))


# --- running the probes over a real session ---------------------------------- #


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@asynccontextmanager
async def _session(stub: StubBackend) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            "OPIK_URL": f"http://127.0.0.1:{stub.port}/api",
            "OPIK_API_KEY": "stub-key",
            "OPIK_WORKSPACE": "stub-workspace",
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
            "OPIK_MCP_LOG_LEVEL": "WARNING",
        },
    )
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


@pytest.mark.e2e
@pytest.mark.anyio
@pytest.mark.parametrize("entity", sorted(ENTITY_REGISTRY))
async def test_the_entitys_description_holds(entity: str, backend: StubBackend) -> None:
    """Every claim this entity's description makes, against the real surface.

    One session per entity rather than one per claim: a claim is a sentence,
    not a scenario, and forty subprocess spawns would buy nothing over
    thirteen. Failures still name the claim, because that is what the message
    is built from.
    """
    claims = [claim for claim in CLAIMS if claim.entity == entity]
    assert claims, f"{entity} has no claims — the table test should have caught this"

    async with _session(backend) as session:
        drive = Driver(session=session, backend=backend)
        for claim in claims:
            try:
                await PROBES[claim.probe](drive)
            except AssertionError as failure:
                raise AssertionError(
                    f"{entity} describes itself as “{claim.phrase}” and the "
                    f"server does not do that.\n  probe: {claim.probe}\n  {failure}"
                ) from failure


# Referenced so the fixtures above are not the only reason these are imported;
# a probe that stops using one should not silently leave a dangling import.
_FIXTURE_IDS = (OTHER_SUITE_ID, SUITE_NAME)

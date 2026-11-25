from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read

from .test_list_tool import FakeOpikClient
from .test_read_tool import FakeOpikClient as FakeReadClient

PROJECT = "019f8d97-c83c-7597-b40a-bd2e0e1ad501"
SPAN = "019f8d97-c83c-7597-b40a-bd2e0e1ad502"
TRACE = "019f8d97-c83c-7597-b40a-bd2e0e1ad503"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _root_spans() -> FakeOpikClient:
    rows = [
        {"id": f"s-{n}", "trace_id": TRACE, "name": "agent", "type": "general"} for n in range(2)
    ]
    return FakeOpikClient(spans={"content": rows, "total": 2, "page": 1, "size": 10})


def _scored_traces() -> FakeOpikClient:
    row = {"id": "t-1", "name": "chat", "feedback_scores": [{"name": "helpfulness", "value": 0.9}]}
    return FakeOpikClient(traces={"content": [row], "total": 1, "page": 1, "size": 10})


def _marker(answer: str) -> str:
    return next(line for line in answer.splitlines() if line.startswith("projected:"))


def _payload(answer: str) -> dict[str, object]:
    parsed: object = json.loads(answer.splitlines()[-1])
    assert isinstance(parsed, dict)
    return parsed


@pytest.mark.anyio
async def test_a_page_of_root_spans_takes_parent_span_id_and_says_no_row_sets_it() -> None:
    out = await run_list(
        "span", project_id=PROJECT, fields=["parent_span_id"], client=_root_spans()
    )
    lines = out.splitlines()
    header = lines.index("id | parent_span_id")
    assert lines[header + 1 : header + 3] == ["s-0 | ", "s-1 | "]
    assert "not set: parent_span_id" in _marker(out)


@pytest.mark.anyio
async def test_a_span_with_no_error_returns_error_info_as_null() -> None:
    fake = FakeReadClient(spans_by_id={SPAN: {"id": SPAN, "name": "llm", "type": "llm"}})
    out = await run_read("span", SPAN, fields=["error_info"], client=fake)
    assert _payload(out) == {"id": SPAN, "error_info": None}
    assert "not set: error_info" in _marker(out)


@pytest.mark.anyio
async def test_a_field_of_an_absent_object_comes_back_as_a_null_object() -> None:
    fake = FakeReadClient(traces_by_id={TRACE: {"id": TRACE, "project_id": PROJECT}})
    out = await run_read("trace", TRACE, fields=["trace.error_info.message"], client=fake)
    assert _payload(out)["trace"] == {"id": TRACE, "error_info": None}
    assert "not set: trace.error_info.message" in _marker(out)


@pytest.mark.anyio
async def test_a_misspelt_field_is_refused_although_no_row_has_the_real_one() -> None:
    with pytest.raises(ToolError) as excinfo:
        await run_list("span", project_id=PROJECT, fields=["parent_spanid"], client=_root_spans())
    message = str(excinfo.value)
    assert "'parent_spanid'" in message
    assert "parent_span_id" in message


@pytest.mark.anyio
async def test_a_misspelt_score_is_refused_with_the_score_the_records_hold() -> None:
    with pytest.raises(ToolError) as excinfo:
        await run_list(
            "trace",
            project_id=PROJECT,
            fields=["feedback_scores.helpfulnes"],
            client=_scored_traces(),
        )
    assert "Did you mean 'feedback_scores.helpfulness'?" in str(excinfo.value)


@pytest.mark.anyio
async def test_a_score_no_record_holds_comes_back_not_set() -> None:
    out = await run_list(
        "trace", project_id=PROJECT, fields=["feedback_scores.toxicity"], client=_scored_traces()
    )
    assert "not set: feedback_scores.toxicity" in _marker(out)


@pytest.mark.anyio
async def test_a_read_returns_one_score_entry_whole_by_its_name() -> None:
    entry = {"name": "helpfulness", "value": 0.4, "reason": "answered the question"}
    fake = FakeReadClient(
        traces_by_id={TRACE: {"id": TRACE, "project_id": PROJECT, "feedback_scores": [entry]}}
    )
    out = await run_read("trace", TRACE, fields=["trace.feedback_scores.helpfulness"], client=fake)
    trace = _payload(out)["trace"]
    assert trace == {"id": TRACE, "feedback_scores": {"helpfulness": entry}}
    assert "not set" not in _marker(out)


@pytest.mark.anyio
async def test_a_read_refuses_a_misspelt_score_with_the_one_it_holds() -> None:
    fake = FakeReadClient(
        traces_by_id={
            TRACE: {
                "id": TRACE,
                "project_id": PROJECT,
                "feedback_scores": [{"name": "helpfulness", "value": 0.4}],
            }
        }
    )
    with pytest.raises(ToolError) as excinfo:
        await run_read("trace", TRACE, fields=["trace.feedback_scores.helpfulnes"], client=fake)
    assert "Did you mean 'trace.feedback_scores.helpfulness'?" in str(excinfo.value)


@pytest.mark.anyio
async def test_a_derived_column_that_no_row_fills_is_taken() -> None:
    out = await run_list(
        "trace", project_id=PROJECT, fields=["experiment_id"], client=_scored_traces()
    )
    assert "not set: experiment_id" in _marker(out)


@pytest.mark.anyio
async def test_a_field_the_span_list_never_sends_is_refused_not_reported_unset() -> None:
    with pytest.raises(ToolError):
        await run_list("span", project_id=PROJECT, fields=["project_name"], client=_root_spans())


@pytest.mark.anyio
async def test_a_path_two_keys_into_metadata_is_refused_not_reported_unset() -> None:
    row = {"id": "t-1", "metadata": {"env": {"region": "eu"}}}
    fake = FakeOpikClient(traces={"content": [row], "total": 1, "page": 1, "size": 10})
    with pytest.raises(ToolError):
        await run_list("trace", project_id=PROJECT, fields=["metadata.env.region"], client=fake)


@pytest.mark.anyio
async def test_a_read_path_into_a_list_of_messages_is_refused_not_reported_unset() -> None:
    fake = FakeReadClient(
        traces_by_id={
            TRACE: {
                "id": TRACE,
                "project_id": PROJECT,
                "input": {"messages": [{"role": "user", "content": "hi"}]},
            }
        }
    )
    with pytest.raises(ToolError):
        await run_read("trace", TRACE, fields=["trace.input.messages.content"], client=fake)


@pytest.mark.anyio
async def test_a_read_does_not_pick_one_span_by_name_out_of_several() -> None:
    fake = FakeReadClient(
        traces_by_id={TRACE: {"id": TRACE, "project_id": PROJECT}},
        trace_spans={TRACE: [{"id": f"s-{n}", "trace_id": TRACE, "name": "llm"} for n in range(2)]},
    )
    with pytest.raises(ToolError):
        await run_read("trace", TRACE, fields=["spans.llm"], client=fake)


@pytest.mark.anyio
async def test_a_sub_field_of_a_column_the_entity_derives_is_refused() -> None:
    row = {
        "id": "e-1",
        "name": "run",
        "prompt_version": {"commit": "abc12345", "prompt_name": "p"},
        "prompt_versions": [{"commit": "abc12345", "prompt_name": "p"}],
    }
    fake = FakeOpikClient(experiments={"content": [row], "total": 1, "page": 1, "size": 10})
    with pytest.raises(ToolError):
        await run_list("experiment", fields=["prompt_version.commit"], client=fake)


def _span_with_metadata(metadata: dict[str, object]) -> FakeReadClient:
    return FakeReadClient(spans_by_id={SPAN: {"id": SPAN, "name": "llm", "metadata": metadata}})


@pytest.mark.anyio
async def test_a_metadata_key_that_holds_dots_is_read_by_its_dotted_path() -> None:
    fake = _span_with_metadata({"gen_ai.request.model": "gpt-4o", "env": "prod"})
    out = await run_read("span", SPAN, fields=["metadata.gen_ai.request.model"], client=fake)
    assert _payload(out) == {"id": SPAN, "metadata": {"gen_ai.request.model": "gpt-4o"}}
    assert "not set" not in _marker(out)


@pytest.mark.anyio
async def test_a_key_that_holds_dots_under_a_nested_object_is_found() -> None:
    fake = FakeReadClient(
        traces_by_id={
            TRACE: {"id": TRACE, "project_id": PROJECT, "metadata": {"ctx": {"user.id": "u-1"}}}
        }
    )
    out = await run_read("trace", TRACE, fields=["trace.metadata.ctx.user.id"], client=fake)
    assert _payload(out)["trace"] == {"id": TRACE, "metadata": {"ctx": {"user.id": "u-1"}}}
    assert "not set" not in _marker(out)


@pytest.mark.anyio
async def test_a_score_whose_name_holds_dots_is_read_by_its_name() -> None:
    entry = {"name": "llm.judge", "value": 1.0}
    fake = FakeReadClient(
        traces_by_id={TRACE: {"id": TRACE, "project_id": PROJECT, "feedback_scores": [entry]}}
    )
    out = await run_read("trace", TRACE, fields=["trace.feedback_scores.llm.judge"], client=fake)
    assert _payload(out)["trace"] == {"id": TRACE, "feedback_scores": {"llm.judge": entry}}
    assert "not set" not in _marker(out)


@pytest.mark.anyio
async def test_a_path_naming_a_flat_and_a_nested_key_is_refused_with_both_quoted() -> None:
    fake = _span_with_metadata({"a.b": 1, "a": {"b": 2}})
    with pytest.raises(ToolError) as excinfo:
        await run_read("span", SPAN, fields=["metadata.a.b"], client=fake)
    message = str(excinfo.value)
    assert """'metadata.["a.b"]'""" in message
    assert """'metadata.["a"]["b"]'""" in message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("option", "metadata"),
    [('metadata.["a.b"]', {"a.b": 1}), ('metadata.["a"]["b"]', {"a": {"b": 2}})],
)
async def test_each_option_the_refusal_names_reads_one_key(
    option: str, metadata: dict[str, object]
) -> None:
    fake = _span_with_metadata({"a.b": 1, "a": {"b": 2}})
    out = await run_read("span", SPAN, fields=[option], client=fake)
    assert _payload(out) == {"id": SPAN, "metadata": metadata}


@pytest.mark.anyio
async def test_a_quoted_path_reads_the_one_key_it_names() -> None:
    fake = _span_with_metadata({"a.b": 1, "a": {"b": 2}})
    out = await run_read("span", SPAN, fields=['metadata.["a.b"]'], client=fake)
    assert _payload(out) == {"id": SPAN, "metadata": {"a.b": 1}}
    assert "metadata.a.b" not in _marker(out).partition("omitted:")[2]

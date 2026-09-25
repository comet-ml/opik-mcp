"""What one call costs when the user has a lot of data.

The fixture crosses every limit a read has: a trace with megabyte-class
bodies, a trace with more spans than a read inlines, a thread with more turns,
a prompt with more versions, a dataset wider than a row shows. For each, the
answer must reach the model, which means staying under what the host accepts
(``HOST_CEILING_CHARS``), and every cut must be declared with a count.

Two reads cross the ceiling today: a whole record is returned whole
(ADR 0002), and the heavy and wide traces are larger than the host takes.
They are strict xfails: the day a change brings them under, the test turns red
and the marker has to go. ADR 0002 records the question as open.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from scripts.seed_e2e_backend import Manifest

from tests.live.conftest import HOST_CEILING_CHARS, Live, continuation

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@dataclass(frozen=True)
class Case:
    tool: str
    args: Callable[[Manifest], dict[str, object]]


_OVER_TODAY = pytest.mark.xfail(
    strict=True, reason="a whole record above the host's cap: ADR 0002, open question"
)

CASES = [
    pytest.param(
        Case("read", lambda m: {"entity_type": "trace", "id": m.tiny.id}), id="tiny-trace"
    ),
    pytest.param(
        Case("read", lambda m: {"entity_type": "trace", "id": m.typical.id}), id="typical-trace"
    ),
    pytest.param(
        Case("read", lambda m: {"entity_type": "trace", "id": m.heavy.id}),
        id="heavy-trace",
        marks=_OVER_TODAY,
    ),
    pytest.param(
        Case("read", lambda m: {"entity_type": "trace", "id": m.wide.id}),
        id="wide-trace",
        marks=_OVER_TODAY,
    ),
    pytest.param(
        Case(
            "read",
            lambda m: {
                "entity_type": "thread",
                "id": m.long_thread.id,
                "project_name": m.project_name,
            },
        ),
        id="long-thread",
    ),
    pytest.param(
        Case("read", lambda m: {"entity_type": "prompt", "id": m.churn_prompt.name}),
        id="long-prompt-history",
    ),
    pytest.param(
        Case("read", lambda m: {"entity_type": "project", "id": m.project_name}), id="project"
    ),
    pytest.param(
        Case(
            "list",
            lambda m: {"entity_type": "trace", "project_name": m.project_name, "size": 100},
        ),
        id="trace-page-of-100",
    ),
    pytest.param(
        Case(
            "list",
            lambda m: {"entity_type": "dataset_item", "dataset_id": m.wide_dataset.id, "size": 100},
        ),
        id="wide-dataset-page-of-100",
    ),
    pytest.param(
        Case(
            "list",
            lambda m: {"entity_type": "thread", "project_name": m.project_name, "size": 100},
        ),
        id="thread-page-of-100",
    ),
]


@pytest.mark.parametrize("case", CASES)
async def test_an_answer_fits_what_the_host_accepts(
    mcp: Live, manifest: Manifest, case: Case
) -> None:
    answer = await mcp.call(case.tool, **case.args(manifest))
    assert not answer.is_error, f"{answer.call} was refused: {answer.text[:300]}"
    assert answer.chars <= HOST_CEILING_CHARS, (
        f"{answer.call} is {answer.chars:,} characters; the host takes {HOST_CEILING_CHARS:,}"
    )


async def test_a_read_states_the_size_of_what_follows(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.read("trace", manifest.heavy.id)
    header, body = answer.text.split("\n", 1)
    stated = int("".join(ch for ch in header.split("|")[1] if ch.isdigit()))
    # The server's own ratio, 2.5 characters per token, which errs high.
    assert stated == pytest.approx(len(body) * 2 / 5, rel=0.01)


async def test_a_wide_trace_accounts_for_every_span(mcp: Live, manifest: Manifest) -> None:
    trace = manifest.wide
    record = (await mcp.read("trace", trace.id)).record()
    spans, more = record["spans"], record.get("moreSpans")
    assert isinstance(spans, list)
    assert isinstance(more, str), "a trace wider than a read must say it was cut"
    shown, total = (int(n) for n in more.split(" spans inlined")[0].split(" of "))
    assert (shown, total) == (len(spans), trace.span_count)
    assert trace.id in more, "the cut must name the call that gets the rest"


async def test_the_call_a_wide_read_names_returns_exactly_the_spans_it_left_out(
    mcp: Live, manifest: Manifest
) -> None:
    trace = manifest.wide
    record = (await mcp.read("trace", trace.id)).record()
    spans, more = record["spans"], record.get("moreSpans")
    assert isinstance(spans, list)
    assert isinstance(more, str), "a trace wider than a read must say it was cut"
    inlined = {s["id"] for s in spans if isinstance(s, dict)}
    rest = await mcp.list(
        "span",
        project_name=manifest.project_name,
        filters=f'trace_id = "{trace.id}"',
        **continuation(more),
    )
    shown = set(rest.column("id"))
    assert (shown & inlined, len(inlined) + len(shown)) == (set(), trace.span_count)


async def test_a_heavy_trace_declares_which_bodies_it_cut(mcp: Live, manifest: Manifest) -> None:
    record = (await mcp.read("trace", manifest.heavy.id)).record()
    note = record.get("spanBodies")
    assert isinstance(note, str), "cut span bodies must be declared"
    assert "read('span'" in note, "and the call that returns one whole"

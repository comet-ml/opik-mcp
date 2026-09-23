"""What a successful write hands back to open.

A write that only says "ok" leaves the person who asked for it where they
started: they know it worked and not where to look. The Diagnostics
operations have always returned a link; these are the rest.

Two rules the tests pin, because both are easy to lose:

- One link per call, never per item. A batch may have changed fifty rows and
  most write targets have no page of their own, so the useful link is to the
  page where the change shows.
- No link is worth a backend call. The project comes from the request or the
  write goes unlinked — a lookup after a successful write can fail, and then
  a write that worked reads as a write that did not.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from opik_mcp.config import Settings
from opik_mcp.writes.operations.observability import decorate_with_page
from opik_mcp.writes.registry import get_operation


def _uuid(n: int) -> UUID:
    """A stable UUID per fixture id, so assertions can name one."""
    return UUID(f"0199c6a4-3a4c-7f1e-9d2b-{n:012d}")


def _settings() -> Settings:
    return Settings(
        opik_api_key="k",
        comet_workspace="demo-ws",
        opik_url="https://opik.test/api/",
    )


class _Item(BaseModel):
    """Stand-in for a validated write model.

    A real one is a pydantic model with a fixed field set per operation. These
    are every field the decorator looks at, across all five operations, each
    optional — so one class stands in for all of them and the list doubles as
    the record of what a link is allowed to be built from.
    """

    # The types are the real models', not convenient ones. An earlier
    # version of this stand-in typed the ids as `str`; the real models store
    # `UUID`, the decorator tested them with `isinstance(v, str)`, and so
    # every one of these tests passed against a production path that emitted
    # no link at all. A fixture that is easier than the thing it stands for
    # tests itself.
    id: UUID | None = None
    trace_id: UUID | None = None
    thread_id: str | None = None
    project_id: UUID | None = None
    project_name: str | None = None
    target: str | None = None
    target_id: str | None = None


def _decorate(operation: str, items: Sequence[BaseModel], project_id: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "operation": operation}
    op = get_operation(operation)
    assert op is not None, operation
    decorate_with_page(op, list(items), out, _settings(), project_id)
    return out


def test_creating_one_trace_links_to_that_trace() -> None:
    out = _decorate("trace.create", [_Item(id=_uuid(1), project_id=_uuid(7))], str(_uuid(7)))
    assert out["url"] == (
        f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=traces&trace={_uuid(1)}"
    )


def test_a_batch_gets_one_link_to_the_page_not_one_per_row() -> None:
    items = [_Item(id=_uuid(n), project_id=_uuid(7)) for n in range(50)]
    out = _decorate("trace.create", items, str(_uuid(7)))
    assert out["url"] == f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=traces"
    assert str(_uuid(0)) not in out["url"]


def test_closing_a_thread_links_to_that_thread() -> None:
    out = _decorate("thread.close", [_Item(thread_id="th-1", project_id=_uuid(7))], str(_uuid(7)))
    assert out["url"] == (
        f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=threads&thread=th-1"
    )


def test_a_span_links_to_the_trace_it_was_added_to() -> None:
    out = _decorate(
        "span.create", [_Item(id=_uuid(2), trace_id=_uuid(1), project_id=_uuid(7))], str(_uuid(7))
    )
    assert out["url"] == (
        f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=traces&trace={_uuid(1)}&span={_uuid(2)}"
    )


def test_a_write_whose_project_is_unknown_still_succeeds_unlinked() -> None:
    """score.create names a trace and no project. Resolving one would mean a
    call after the write has already happened, and a failure there would read
    as a failed write."""
    out = _decorate("score.create", [_Item(target="trace", target_id=str(_uuid(1)))], None)
    assert out["ok"] is True
    assert "url" not in out


@pytest.mark.parametrize("operation", ["trace.create", "trace.update", "span.create"])
def test_no_link_without_a_project(operation: str) -> None:
    out = _decorate(operation, [_Item(id=_uuid(9))], None)
    assert "url" not in out


def test_the_project_is_read_off_the_payload_when_nothing_resolved_one() -> None:
    """These operations resolve nothing before sending, so the request itself
    is where the project comes from."""
    out = _decorate("trace.create", [_Item(id=_uuid(1), project_id=_uuid(7))], None)
    assert out["url"].startswith(f"https://opik.test/demo-ws/projects/{_uuid(7)}/")


def test_a_project_named_but_not_identified_is_not_resolved_for_a_link() -> None:
    """project_name would have to become an id, and that is a call — after the
    write already succeeded. The write stands; the link does not."""
    out = _decorate("trace.create", [_Item(id=_uuid(1), project_name="checkout")], None)
    assert out["ok"] is True
    assert "url" not in out


def test_scoring_a_trace_links_to_that_trace() -> None:
    """The ticket's own example of the flow: "score this trace 0.8 on
    helpfulness". The annotation models carry the project and the target, so
    nothing needs looking up."""
    out = _decorate(
        "score.create",
        [_Item(target="trace", target_id=str(_uuid(1)), project_id=_uuid(7))],
        None,
    )
    assert out["url"] == (
        f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=traces&trace={_uuid(1)}"
    )


def test_commenting_on_a_thread_links_to_that_thread() -> None:
    out = _decorate(
        "comment.create",
        [_Item(target="thread", target_id="th-1", project_id=_uuid(7))],
        None,
    )
    assert out["url"] == (
        f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=threads&thread=th-1"
    )


def test_scoring_a_span_lands_on_the_spans_view_it_cannot_open() -> None:
    """A span is only addressable inside its trace, and an annotation names
    the span without naming the trace. So the link is the page, not the row —
    the same tier a score name gets, for the same reason."""
    out = _decorate(
        "score.create",
        [_Item(target="span", target_id=str(_uuid(2)), project_id=_uuid(7))],
        None,
    )
    assert out["url"] == (f"https://opik.test/demo-ws/projects/{_uuid(7)}/logs?logsType=spans")

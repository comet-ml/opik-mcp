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

import pytest
from pydantic import BaseModel

from opik_mcp.config import Settings
from opik_mcp.writes.operations.observability import decorate_with_page
from opik_mcp.writes.registry import get_operation


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

    id: str | None = None
    trace_id: str | None = None
    thread_id: str | None = None
    project_id: str | None = None
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
    out = _decorate("trace.create", [_Item(id="t-1", project_id="p-7")], "p-7")
    assert out["url"] == ("https://opik.test/demo-ws/projects/p-7/logs?logsType=traces&trace=t-1")


def test_a_batch_gets_one_link_to_the_page_not_one_per_row() -> None:
    items = [_Item(id=f"t-{n}", project_id="p-7") for n in range(50)]
    out = _decorate("trace.create", items, "p-7")
    assert out["url"] == "https://opik.test/demo-ws/projects/p-7/logs?logsType=traces"
    assert "t-0" not in out["url"]


def test_closing_a_thread_links_to_that_thread() -> None:
    out = _decorate("thread.close", [_Item(thread_id="th-1", project_id="p-7")], "p-7")
    assert out["url"] == (
        "https://opik.test/demo-ws/projects/p-7/logs?logsType=threads&thread=th-1"
    )


def test_a_span_links_to_the_trace_it_was_added_to() -> None:
    out = _decorate("span.create", [_Item(id="s-1", trace_id="t-1", project_id="p-7")], "p-7")
    assert out["url"] == (
        "https://opik.test/demo-ws/projects/p-7/logs?logsType=traces&trace=t-1&span=s-1"
    )


def test_a_write_whose_project_is_unknown_still_succeeds_unlinked() -> None:
    """score.create names a trace and no project. Resolving one would mean a
    call after the write has already happened, and a failure there would read
    as a failed write."""
    out = _decorate("score.create", [_Item(target="trace", target_id="t-1")], None)
    assert out["ok"] is True
    assert "url" not in out


@pytest.mark.parametrize("operation", ["trace.create", "trace.update", "span.create"])
def test_no_link_without_a_project(operation: str) -> None:
    out = _decorate(operation, [_Item(id="x-1")], None)
    assert "url" not in out


def test_the_project_is_read_off_the_payload_when_nothing_resolved_one() -> None:
    """These operations resolve nothing before sending, so the request itself
    is where the project comes from."""
    out = _decorate("trace.create", [_Item(id="t-1", project_id="p-7")], None)
    assert out["url"].startswith("https://opik.test/demo-ws/projects/p-7/")


def test_a_project_named_but_not_identified_is_not_resolved_for_a_link() -> None:
    """project_name would have to become an id, and that is a call — after the
    write already succeeded. The write stands; the link does not."""
    out = _decorate("trace.create", [_Item(id="t-1", project_name="checkout")], None)
    assert out["ok"] is True
    assert "url" not in out

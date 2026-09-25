"""Conformance — the exact-tool-inventory rule.

`tools/list` is part of the public MCP contract. Adding a tool is a
major-version change (every host caches the list); removing one breaks
any agent that has a prompt pinned to it. We pin the set here so an
accidental `@mcp.tool` either ships intentionally with a snapshot
update, or fails CI.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.config import Settings
from opik_mcp.instructions import render_instructions
from opik_mcp.server import mcp

# Ceiling on everything `tools/list` advertises: names + descriptions + input
# schemas, as sent on the wire.
#
# Raising this is a real decision, not a formality. The tool surface is resident
# in the context of EVERY request the host makes, including the ones that never
# touch Opik, so a bigger surface is the user's context spent on our behalf
# whether they use us that turn or not. Prefer trimming a description or moving
# reference material behind `schema()`, which is fetched only when wanted.
#
# History, with who spent what — the point of keeping it is that the next
# raise can see whether the surface is growing for one reason or for many:
#   15,444  2026-09-09, when this test was added.
#   17,498  OPIK-8284 — the project overview and the metric series. +2,054
#           bytes (~513 tokens): `project_metric` on `list` with its
#           metric/interval/breakdown params (~1.5k), two drill-down entity
#           names, and the read tool's project shape.
#   19,404  merging OPIK-8310 (Diagnostics disabled fallback). +1,906 bytes,
#           almost all of it `write`, which grew 3,644 → 5,403 for the
#           `agent_insights_job.trigger` operation, plus `schema` 910 → 1,057.
#           That change predates this test, so nothing measured it at the time;
#           this is the guard's first contact with a surface change that was
#           not its author's.
#   19,647  OPIK-8284 — `series` on `list`. +243 bytes for one optional string,
#           weighed against grouping being a dead end for eight of the thirteen
#           groupable metrics: the backend requires a sub-metric for duration,
#           feedback-score and token-usage metrics whenever a breakdown is
#           asked for, and without the argument the agent could read the
#           refusal and not act on it. Paid for in retries otherwise.
#   20,314  OPIK-8284 — the interval rule on `list.interval`, the examples on
#           `since` put back, and a line on `read` saying that a long inlined
#           collection carries the call for its remainder. +667 bytes, all of
#           it wording rather than arguments.
#   20,770  OPIK-8393 — comparing experiments case by case. +456 bytes: the
#           `experiment_ids` array on `list` (~330) with the rest on the
#           `dataset_id` line that now says when it is optional, and the
#           entity list saying what experiment_ids does. The argument is the
#           whole feature — without it the agent's only route to "which cases
#           regressed" is reading every trace of both runs — so this is the
#           kind of spend the budget exists to permit, not to prevent.
#   20,673  OPIK-8393 — the `test_suite` entity renamed to `dataset` across
#           read, list, write and schema, with `dataset.create` gaining `type`
#           so both kinds can be made. -97 bytes: the shorter name paid for the
#           new field. The old names still resolve as unadvertised aliases.
#   21,361  measured at the head of OPIK-8399, before it. Nothing in this table
#           accounts for the 688 bytes between this and the line above it:
#           OPIK-8396 grew `list` and `read` without noting it, the same way
#           `write` did before the guard existed. Recorded so the next raise
#           does not read someone else's spend as its own.
#   21,394  after OPIK-8401 (#198), which spent 33 bytes on `read_skill`.
#   21,694  after OPIK-8397 (#196), which spent 300 on making a case findable
#           and readable: `dataset_item` joins the `read` enum, and `list`
#           carries the case vocabulary.
#   22,633  OPIK-8399 — `fields` on `read` and `list`. +939 bytes, of which
#           551 are the two argument descriptions, ~290 the array schema
#           Pydantic emits for each, and 102 a correction: `read`'s own
#           description said the record it returns is never truncated, which
#           this ticket makes conditional. This is the argument that makes one
#           field of one record askable at all — the ticket's measurement is a
#           trace read costing 8,000-10,000 tokens when one span's output was
#           wanted, against 33 tokens for the same question asked with
#           `fields`. A surface that buys itself back on its first use is the
#           spend this budget exists to permit.
#   22,811  measured at the head of OPIK-8485, before it. The 178 bytes above
#           22,633 landed on main without a line here (#199, #201).
#   23,495  OPIK-8485 — the report now counts each tool's title and hints,
#           +684 bytes. Hosts read them without the schema; Claude Code runs
#           read-only tools in parallel because of them.
#   23,396  OPIK-8496 — `write`'s note on `validation_failed` stops promising
#           the inlined JSON Schema, which the envelope no longer carries.
#           -99 bytes.
#   23,280  OPIK-8496 — `read`'s name-lookup lines and the upsert operation's
#           line say what happens instead of what to prefer. -116 bytes.
#   23,258  OPIK-8496 — `list.project_name` drops "so you don't need to";
#           the one input-schema change in the ticket. -22 bytes.
#
# The ceiling used to sit ~400 bytes above the measurement. That proved to be
# the wrong slack: it was hit three times inside one ticket, and each time the
# trade was a sentence the agent needed for a handful of bytes — the examples
# on `since` went from four to two, the interval rule lost its comparison to
# the UI, and the pointer that turns a truncated span tree into its next page
# went unmentioned. A guard that taxes every improvement gets routed around;
# a guard against the multi-kilobyte surprise (the `write` entry in the table
# above landed 1.9 KB in a PR nobody measured) still earns its keep. So the
# ceiling was set ~3.5 KB above the measurement of the day (#187): room for a
# ticket's worth of wording, not for a new tool or an operation nobody meant
# to advertise. The table above has spent most of it since. At 23,495 the
# headroom was 505 bytes; at 23,258 it is 742. Unused headroom is not in
# anyone's context; only what is written is.
SURFACE_BUDGET_BYTES = 24_000


def surface_report(
    advertised: Iterable[tuple[str, str, dict[str, Any], dict[str, Any]]],
) -> tuple[int, str]:
    """Total advertised bytes, plus a per-tool breakdown biggest-first.

    Counts what the host actually caches for each tool — its name, its
    description, its input schema, and its title and hints. The frozen
    snapshot files cover only the schemas, so they would miss growth in the
    descriptions, which is where an added entity type mostly lands.
    """
    rows = [
        (
            name,
            len(name)
            + len(description)
            + len(json.dumps(schema, separators=(",", ":")))
            + (len(json.dumps(metadata, separators=(",", ":"))) if metadata else 0),
        )
        for name, description, schema, metadata in advertised
    ]
    rows.sort(key=lambda row: row[1], reverse=True)
    total = sum(size for _, size in rows)
    report = "\n".join(f"  {name:<12} {size:>6} bytes" for name, size in rows)
    return total, report


EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "list",
        "write",
        "schema",
        # OPIK-7472. Resources carry the same skills, but resource browsing is a
        # host capability rather than a model one — on hosts that never surface
        # resources to the LLM, this tool is the only way an agent reaches them.
        "read_skill",
    }
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tools_list_advertises_exactly_the_phase_one_surface() -> None:
    """An accidental `@mcp.tool` would silently expand the public surface;
    a typo on a tool name would silently rename one. Pin both."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    advertised = {t.name for t in tools.tools}
    assert advertised == EXPECTED_TOOLS, (
        f"tool surface drift: advertised={sorted(advertised)} expected={sorted(EXPECTED_TOOLS)}"
    )


@pytest.mark.anyio
async def test_every_tool_has_nonempty_description() -> None:
    """Some strict hosts (Cursor, MCP Inspector strict mode) reject tools
    with no description. A regression that ships an undocumented tool
    would silently disable it on those hosts."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    missing = [t.name for t in tools.tools if not (t.description or "").strip()]
    assert not missing, f"tools missing descriptions: {missing}"


@pytest.mark.anyio
async def test_advertised_tool_surface_stays_within_budget() -> None:
    """Every byte here rides in the context window of every request the host
    makes — including requests that have nothing to do with Opik. Growth is
    spending the user's context without their consent, so it is capped and
    the cap is a visible review decision."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    total, report = surface_report(
        (
            t.name,
            t.description or "",
            t.inputSchema,
            {
                "title": t.title,
                "annotations": t.annotations.model_dump(exclude_none=True)
                if t.annotations
                else None,
            },
        )
        for t in tools.tools
    )
    assert total <= SURFACE_BUDGET_BYTES, (
        f"advertised tool surface is {total} bytes, over the "
        f"{SURFACE_BUDGET_BYTES}-byte budget by {total - SURFACE_BUDGET_BYTES}.\n"
        f"{report}\n"
        "Trim a description, move reference material into schema(), or raise "
        "SURFACE_BUDGET_BYTES deliberately with a note saying why."
    )


def test_budget_report_counts_name_description_and_schema() -> None:
    """The budget is only meaningful if it measures what actually ships. A
    tool costs its name, its description AND its input schema; measuring the
    schema alone (what the snapshot files hold) would miss the descriptions,
    which are the half that grows when an entity is added."""
    total, report = surface_report(
        [
            ("aa", "desc", {"type": "object"}, {}),
            ("bbbb", "", {}, {"title": "B"}),
        ]
    )
    # names 2 + 4, descriptions 4 + 0, schemas '{"type":"object"}' + '{}',
    # metadata '{"title":"B"}'
    assert total == 2 + 4 + 4 + 0 + 17 + 2 + 13
    assert "aa" in report
    assert "bbbb" in report


def test_budget_report_names_the_biggest_tool_first() -> None:
    """On a failure the reviewer needs to see which tool to trim, not an
    alphabetical list they have to scan."""
    _, report = surface_report(
        [
            ("small", "x", {}, {}),
            ("huge", "x" * 500, {}, {}),
        ]
    )
    lines = [line for line in report.splitlines() if line.strip()]
    assert "huge" in lines[0], report


# The instructions a host receives on `initialize`. With tool search on, the
# tool list above is deferred but this text is not: every session that loads
# the server carries it. Measured 4,750 bytes (about 1,150 tokens) with a long
# workspace name and email, OPIK-8485. 4,759 at the head of OPIK-8496, then
# 4,711 after it reworded three imperatives as statements (-48). Raise it on
# purpose, with a note here.
# This caps growth; the host's 2,048-character cut on the same text is pinned
# in test_tool_annotations.py. Lower this to match once the text fits.
INSTRUCTIONS_BUDGET_BYTES = 5_000


def longest_instructions() -> str:
    return render_instructions(
        Settings(comet_workspace="w" * 40, opik_url="https://www.comet.com/opik/api"),
        user_email="u" * 40 + "@example.com",
    )


def test_instructions_stay_within_budget() -> None:
    size = len(longest_instructions().encode())
    assert size <= INSTRUCTIONS_BUDGET_BYTES, (
        f"instructions are {size} bytes, over the {INSTRUCTIONS_BUDGET_BYTES}-byte budget. "
        "Every session that loads the server pays this; move detail into schema() or a skill."
    )

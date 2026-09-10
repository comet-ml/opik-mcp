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
#
# The ceiling sits ~400 bytes above the measurement on purpose. A budget with no
# slack is a budget that gets raised in a hurry inside an unrelated PR the first
# time someone fixes a typo in a description; this one still catches a real
# regression while leaving room to reword. The slack costs nothing — a ceiling
# is not an allocation, and unused headroom is not in anyone's context.
SURFACE_BUDGET_BYTES = 20_050


def surface_report(
    advertised: Iterable[tuple[str, str, dict[str, Any]]],
) -> tuple[int, str]:
    """Total advertised bytes, plus a per-tool breakdown biggest-first.

    Counts what the host actually caches for each tool — its name, its
    description and its input schema. The frozen snapshot files cover only
    the schemas, so they would miss growth in the descriptions, which is
    where an added entity type mostly lands.
    """
    rows = [
        (name, len(name) + len(description) + len(json.dumps(schema, separators=(",", ":"))))
        for name, description, schema in advertised
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
        (t.name, t.description or "", t.inputSchema) for t in tools.tools
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
            ("aa", "desc", {"type": "object"}),
            ("bbbb", "", {}),
        ]
    )
    # names 2 + 4, descriptions 4 + 0, schemas '{"type":"object"}' + '{}'
    assert total == 2 + 4 + 4 + 0 + 17 + 2
    assert "aa" in report
    assert "bbbb" in report


def test_budget_report_names_the_biggest_tool_first() -> None:
    """On a failure the reviewer needs to see which tool to trim, not an
    alphabetical list they have to scan."""
    _, report = surface_report(
        [
            ("small", "x", {}),
            ("huge", "x" * 500, {}),
        ]
    )
    lines = [line for line in report.splitlines() if line.strip()]
    assert "huge" in lines[0], report

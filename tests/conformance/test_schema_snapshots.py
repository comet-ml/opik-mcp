"""Conformance — frozen `inputSchema` snapshots for every advertised tool.

Tool input schemas are the single most-cached piece of the MCP contract.
A host that fetched `tools/list` yesterday and re-uses the cached
schemas today will silently reject any request that doesn't match — and
the resulting "the tool doesn't work for me" reports are notoriously
hard to reproduce.

Snapshot-on-disk gives reviewers a diff in the PR for every schema
change. The diff is the point: an intentional change still ships, but
nobody can change a tool's wire shape without it surfacing on review.

To regenerate after an intentional change:

    UPDATE_SNAPSHOTS=1 uv run pytest tests/conformance/test_schema_snapshots.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# The tools whose schemas we freeze. Kept in sync with
# `test_tool_inventory.EXPECTED_TOOLS` via the parametrize list; a drift
# there will fail the inventory test first.
TOOLS = (
    "read",
    "list",
    "write",
    "schema",
    "ask_ollie",
    "run_experiment",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _fetch_input_schema(tool: str) -> dict[str, Any]:
    """One MCP session per call. Function-scoped to play nicely with anyio's
    function-scoped backend fixture; sessions over the in-memory transport
    are sub-millisecond so the cost is invisible."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    advertised = {t.name: t.inputSchema for t in tools.tools}
    assert tool in advertised, f"{tool} not advertised over the wire — see test_tool_inventory.py"
    return advertised[tool]


def _snapshot_path(tool: str) -> Path:
    return SNAPSHOT_DIR / f"{tool}.json"


def _canonicalize(value: Any) -> str:
    """Stable JSON form so a dict-order shuffle doesn't trigger a diff."""
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


@pytest.mark.anyio
@pytest.mark.parametrize("tool", TOOLS)
async def test_tool_schema_matches_snapshot(tool: str) -> None:
    """Each tool's `inputSchema` MUST match the on-disk snapshot byte-for-byte.

    A failure here means the wire schema has changed — either accept it
    (regenerate the snapshot and commit) or revert the offending edit.
    """
    actual = await _fetch_input_schema(tool)
    path = _snapshot_path(tool)

    if os.getenv("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonicalize(actual), encoding="utf-8")
        pytest.skip(f"snapshot updated: {path.name}")

    if not path.exists():
        pytest.fail(
            f"missing snapshot for {tool!r}; first time? run "
            f"`UPDATE_SNAPSHOTS=1 pytest {__name__}` to create "
            f"{path.relative_to(Path.cwd())}."
        )

    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"{tool}: inputSchema drift vs. snapshot — "
        f"diff visible in `git diff {path.relative_to(Path.cwd())}` "
        "after running with UPDATE_SNAPSHOTS=1. If the change is "
        "intentional, commit the snapshot in the same PR."
    )

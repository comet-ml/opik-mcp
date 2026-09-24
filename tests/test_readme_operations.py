"""The README's tool and write-operation tables equal what the server serves.

The README is the first thing a user reads and the last thing a PR remembers
to update. Both tables are parsed from the markdown, so a row added, renamed or
forgotten on either side turns this red.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp
from opik_mcp.writes.registry import WRITE_OPERATIONS

README = Path(__file__).resolve().parent.parent / "README.md"


def _first_column_of_table_under(heading: str) -> set[str]:
    """Backticked names in the first column of the first table after `heading`."""
    text = README.read_text(encoding="utf-8")
    after_heading = text[text.index(heading) + len(heading) :]
    table = re.search(r"(?:^\|.*\n)+", after_heading, flags=re.MULTILINE)
    assert table, f"no table under {heading!r} in README.md"
    return set(re.findall(r"^\| \[?`([^`]+)`", table.group(0), flags=re.MULTILINE))


def test_the_readme_write_table_names_exactly_the_registered_operations() -> None:
    documented = _first_column_of_table_under("### `write`")
    registered = set(WRITE_OPERATIONS)
    assert documented == registered, (
        f"README write table drift: missing={sorted(registered - documented)} "
        f"stale={sorted(documented - registered)}"
    )


@pytest.mark.anyio
async def test_the_readme_tool_table_names_exactly_the_advertised_tools() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    advertised = {t.name for t in tools.tools}
    documented = _first_column_of_table_under("## Tools")
    assert documented == advertised, (
        f"README tool table drift: missing={sorted(advertised - documented)} "
        f"stale={sorted(documented - advertised)}"
    )

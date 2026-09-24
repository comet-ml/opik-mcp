"""The live suite's harness: a seeded Opik, and the MCP server driven over stdio.

Tests go in through the door a host uses and no other: a real ``python -m
opik_mcp`` subprocess, a real MCP client session, a real backend. Nothing here
imports ``opik_mcp``. The suite depends on the five tool names, their argument
names and the answer shapes, which the conformance snapshots already pin, so a
refactor that moves modules around leaves it untouched.

What the backend holds comes from ``scripts/seed_e2e_backend.py``: the session
fixture finds the fixture or seeds it, and hands every test its manifest. A
test asserts exact values from the manifest, never a hardcoded id.

Environment:

- ``OPIK_URL``: the REST base of the backend, e.g. ``http://localhost:8080``.
  Required; the suite fails in one line when nothing answers there.
- ``OPIK_API_KEY``, ``OPIK_WORKSPACE``: passed to the server and the seed as is.
- ``OPIK_LIVE_SHARED=1``: a shared workspace. The session never seeds or
  wipes the fixture, it loads the one seeded there by hand. Writes still run:
  they touch only their own run's records.
- ``OPIK_LIVE_SIZES``: a file to append each answer's size to, as markdown
  table rows. CI points it at the job summary.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from scripts.seed_e2e_backend import (
    RUN_PREFIX,
    Backend,
    Manifest,
    SeedError,
    delete_named,
    load,
    seed,
    sweep_runs,
)

#: A hung server must fail the test, not the job.
_TIMEOUT_S = 120

#: Claude Code rejects an MCP result above 25,000 tokens by default (ADR 0002).
#: The server estimates 2.5 characters per token, erring high, so this is the
#: largest answer that is known to reach the model. A test contract, not a
#: server constant: when ADR 0002 decides the per-tool limit, this follows it.
HOST_CEILING_CHARS = 25_000 * 5 // 2

#: A run older than this that left records behind has died; a live one has not.
_STALE_RUN = timedelta(hours=2)

_SHOWING = re.compile(r"showing (\d+) of (\d+)")


# --- the backend and its fixture ----------------------------------------------


def _shared() -> bool:
    return os.environ.get("OPIK_LIVE_SHARED") == "1"


@pytest.fixture(scope="session")
def backend() -> Iterator[Backend]:
    try:
        client = Backend.from_env()
    except SeedError as err:
        pytest.fail(f"{err} Start one: see docs/live-e2e/design-doc.md.", pytrace=False)
    if not _shared() and not client.ready():
        client.close()
        pytest.fail(
            f"no Opik backend is ready at {client.base_url}. "
            "Start one: see docs/live-e2e/design-doc.md.",
            pytrace=False,
        )
    yield client
    client.close()


@pytest.fixture(scope="session")
def manifest(backend: Backend) -> Manifest:
    try:
        found = load(backend) if _shared() else seed(backend)
    except SeedError as err:
        pytest.fail(f"the fixture is not usable: {err}", pytrace=False)
    if found is None:
        pytest.fail(
            "the shared workspace holds no fixture; seed it once with "
            "scripts/seed_e2e_backend.py --ids-at-seed-time",
            pytrace=False,
        )
    return found


@pytest.fixture
def windowed(manifest: Manifest) -> Manifest:
    """The manifest, for a test that needs ids that carry their record's time."""
    if not manifest.ids_carry_time:
        pytest.skip("the fixture's ids were minted at seeding time, so windows cannot see it")
    return manifest


@pytest.fixture(scope="session")
def run_prefix(backend: Backend, manifest: Manifest) -> Iterator[str]:
    """The name this run's writes carry, and their cleanup.

    Every record a write test creates is named with it, and nothing a read
    asserts is, so writes never touch the fixture. Before the run, leftovers of
    runs that died before cleaning up are swept; after it, this run's go.
    """
    sweep_runs(backend, older_than=_STALE_RUN)
    prefix = f"{RUN_PREFIX}{os.getpid()}-{os.urandom(3).hex()}"
    yield prefix
    delete_named(backend, prefix)


def continuation(note: str) -> dict[str, int]:
    """The page and size a declared cut names as the call that gets the rest."""
    found = re.search(r"page=(\d+), size=(\d+)", note)
    assert found, f"the cut names no page to continue from: {note}"
    return {"page": int(found[1]), "size": int(found[2])}


def new_id() -> str:
    """A UUIDv7 for now, the id the backend requires on records a write creates."""
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | (0x7 << 76) | ((rand >> 64) & 0x0FFF) << 64 | (0b10 << 62)
    return str(uuid.UUID(int=value | rand & 0x3FFFFFFFFFFFFFFF))


# --- answers ------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """One tool result, as the host would hand it to the model."""

    call: str
    text: str
    is_error: bool

    @property
    def chars(self) -> int:
        return len(self.text)

    def record(self) -> dict[str, object]:
        """A read's JSON body, below its size header and any notes."""
        assert not self.is_error, f"{self.call} was refused: {self.text[:300]}"
        start = next((i for i, line in enumerate(self.text.split("\n")) if line[:1] == "{"), None)
        assert start is not None, f"{self.call} has no JSON body: {self.text[:300]}"
        parsed: object = json.loads("\n".join(self.text.split("\n")[start:]))
        assert isinstance(parsed, dict), f"{self.call} body is not an object"
        return {str(k): v for k, v in parsed.items()}

    def rows(self) -> list[dict[str, str]]:
        """A list answer's table, one dict per row, cells as the host sees them."""
        assert not self.is_error, f"{self.call} was refused: {self.text[:300]}"
        lines = self.text.split("\n")
        found = next((i for i, line in enumerate(lines) if line.startswith("Found ")), None)
        if found is not None:
            blank = next(i for i in range(found, len(lines)) if not lines[i].strip())
            head = blank + 1
        else:
            head = next(i for i, line in enumerate(lines) if line and not line.startswith("["))
        columns = [c.strip() for c in lines[head].split(" | ")]
        table: list[dict[str, str]] = []
        for line in lines[head + 1 :]:
            if not line.strip():
                break
            cells = [c.strip() for c in line.split(" | ")]
            table.append(dict(zip(columns, cells, strict=True)))
        return table

    def column(self, name: str) -> list[str]:
        return [row[name] for row in self.rows()]

    def total(self) -> int:
        """How many rows the backend has for the query, across every page."""
        match = _SHOWING.search(self.text)
        assert match, f"{self.call} states no total: {self.text[:300]}"
        return int(match.group(2))


class Live:
    """A client session on a freshly spawned server, with size bookkeeping."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call(self, tool: str, /, **args: object) -> Answer:
        result = await self._session.call_tool(tool, args)
        text = "".join(part.text for part in result.content if hasattr(part, "text"))
        shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
        answer = Answer(f"{tool}({shown})", text, bool(result.isError))
        _record_size(answer)
        return answer

    async def read(self, entity_type: str, id: str, **args: object) -> Answer:
        return await self.call("read", entity_type=entity_type, id=id, **args)

    async def list(self, entity_type: str, **args: object) -> Answer:
        return await self.call("list", entity_type=entity_type, **args)

    async def write(self, operation: str, data: object) -> dict[str, object]:
        answer = await self.call("write", operation=operation, data=data)
        assert not answer.is_error, f"{answer.call} was refused: {answer.text[:500]}"
        parsed: object = json.loads(answer.text)
        assert isinstance(parsed, dict), f"{answer.call} did not answer with an object"
        assert parsed.get("ok") is True, f"{answer.call} did not succeed: {answer.text[:500]}"
        return {str(k): v for k, v in parsed.items()}


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        # The interpreter running the tests, so the server runs this checkout.
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            # Restated where they take effect: this server is a child process.
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
            "OPIK_MCP_LOG_LEVEL": "WARNING",
        },
    )


@asynccontextmanager
async def _session() -> AsyncIterator[Live]:
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(_server_params()) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield Live(session)


@pytest.fixture
async def mcp(manifest: Manifest) -> AsyncIterator[Live]:
    """A server session per test, started after the fixture is in place."""
    async with _session() as live:
        yield live


# --- sizes --------------------------------------------------------------------


_SIZES_HEADER = "\n### Answer sizes\n\n| call | characters |\n|---|---|\n"


def _record_size(answer: Answer) -> None:
    target = os.environ.get("OPIK_LIVE_SIZES")
    if not target:
        return
    path = Path(target)
    # Checked in the file rather than a flag, so no state outlives a test.
    started = path.exists() and "### Answer sizes" in path.read_text()
    call = answer.call if len(answer.call) <= 120 else f"{answer.call[:117]}..."
    with path.open("a") as out:
        if not started:
            out.write(_SIZES_HEADER)
        out.write(f"| `{call.replace('|', '/')}` | {answer.chars:,} |\n")

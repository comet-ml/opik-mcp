"""Per-call session-context block on tool_called / ask_ollie_completed.

BI segments per-call events by real-user cohort (``is_ci`` / ``is_container``)
and MCP host on a single table, without joining each call back to
``server_started`` / ``session_initialized`` on ``install_id`` (a join that
drops ~35% of calls). These tests pin the 6-field block, its caching, and the
allowlist contract (bucketed enums / boolean strings only).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from opik_mcp.analytics import EVENT_TOOL_CALLED
from opik_mcp.analytics.environment import cached_call_context_env
from opik_mcp.analytics.events import HostLlmFamily, LaunchMethod, McpHost
from opik_mcp.analytics.mcp_client_info import (
    _reset_call_context_cache_for_tests,
    call_context_props,
)
from opik_mcp.analytics.wrappers import (
    _reset_seen_sessions_for_tests,
    instrument_tool,
)

CONTEXT_KEYS = {
    "is_ci",
    "is_container",
    "launch_method",
    "install_id_freshly_generated",
    "mcp_host",
    "host_llm_family",
}

ENV_KEYS = {"is_ci", "is_container", "launch_method", "install_id_freshly_generated"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    _reset_call_context_cache_for_tests()
    _reset_seen_sessions_for_tests()
    yield
    _reset_call_context_cache_for_tests()
    _reset_seen_sessions_for_tests()


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    return r


class _Session:
    """Weak-referenceable session stand-in (so the per-session host cache
    actually engages — ``SimpleNamespace`` doesn't support weakref on 3.13)."""

    def __init__(self, name: str = "", version: str = "") -> None:
        client_info = SimpleNamespace(name=name, version=version)
        self.client_params = SimpleNamespace(
            clientInfo=client_info, protocolVersion="", capabilities=None
        )


# --- cached env ----------------------------------------------------------- #


def test_cached_call_context_env_keys_and_allowlist() -> None:
    env = cached_call_context_env()
    assert set(env) == ENV_KEYS
    assert env["is_ci"] in {"true", "false"}
    assert env["is_container"] in {"true", "false", "unknown"}
    assert env["launch_method"] in set(get_args(LaunchMethod))
    assert env["install_id_freshly_generated"] in {"true", "false"}


def test_cached_call_context_env_is_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolved once per process: flipping an env var after the first call must
    not change the cached answer (proves we don't re-probe per call)."""
    first = cached_call_context_env()
    monkeypatch.setenv("CI", "1")
    assert cached_call_context_env() is first


# --- call_context_props --------------------------------------------------- #


def test_call_context_props_buckets_known_host() -> None:
    props = call_context_props(_Session(name="claude-code", version="1.2.3"))
    assert set(props) == CONTEXT_KEYS
    assert props["mcp_host"] == "claude-code"
    assert props["host_llm_family"] == "anthropic"


def test_call_context_props_handles_none_session() -> None:
    props = call_context_props(None)
    assert set(props) == CONTEXT_KEYS
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"


def test_call_context_props_unknown_host_buckets_to_other() -> None:
    props = call_context_props(_Session(name="acme-internal-wrapper-bob"))
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"
    # Every value is from an allowlist — nothing host-controlled survives.
    assert props["mcp_host"] in set(get_args(McpHost))
    assert props["host_llm_family"] in set(get_args(HostLlmFamily))


def test_host_context_cached_per_session() -> None:
    """clientInfo is read and bucketed once; mutating the live session after
    the first read must not change the cached host block."""
    sess = _Session(name="claude-code")
    first = call_context_props(sess)
    assert first["mcp_host"] == "claude-code"

    sess.client_params.clientInfo.name = "cursor"
    second = call_context_props(sess)
    assert second["mcp_host"] == "claude-code"


def test_host_context_non_weakref_session_recomputes() -> None:
    """A session that doesn't support weakref (SimpleNamespace) must not crash
    the cache path — it falls through and recomputes."""
    sess = SimpleNamespace(
        client_params=SimpleNamespace(
            clientInfo=SimpleNamespace(name="cursor", version=""),
            protocolVersion="",
            capabilities=None,
        )
    )
    props = call_context_props(sess)
    assert props["mcp_host"] == "cursor"
    assert props["host_llm_family"] == "cursor"


# --- integration: tool_called -------------------------------------------- #


@pytest.mark.anyio
async def test_tool_called_carries_session_context(recorder: _Recorder) -> None:
    @instrument_tool("read")
    async def fn(*, ctx: Any) -> str:
        return "ok"

    ctx = SimpleNamespace(session=_Session(name="cursor"))
    await fn(ctx=ctx)

    props = next(p for et, p in recorder.events if et == EVENT_TOOL_CALLED)
    assert set(props) >= CONTEXT_KEYS
    assert props["mcp_host"] == "cursor"
    assert props["host_llm_family"] == "cursor"
    assert props["is_ci"] in {"true", "false"}


@pytest.mark.anyio
async def test_tool_called_context_defaults_without_ctx(recorder: _Recorder) -> None:
    @instrument_tool("hello")
    async def fn() -> str:
        return "x"

    await fn()

    props = next(p for et, p in recorder.events if et == EVENT_TOOL_CALLED)
    assert set(props) >= CONTEXT_KEYS
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"

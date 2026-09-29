"""Refusals in cost intelligence mode never name what the mode hides."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp import skills_catalog as catalog
from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.cost_intelligence import COST_INTELLIGENCE_MODE, WORKSPACE_PREFIX
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.writes.schema_tool import run_schema
from tests.factories import make_settings

pytestmark = pytest.mark.anyio

SPEND = make_settings(opik_workspace=f"{WORKSPACE_PREFIX}org__", opik_api_key="k")
DEFAULT = make_settings(opik_workspace="team", opik_api_key="k")
UUID = "0190a3c4-1111-7000-8000-000000000001"
HIDDEN = (
    "dataset",
    "dataset_item",
    "experiment",
    "prompt",
    "prompt_version",
    "agent_insights_issue",
    "test_suite",
    "issue",
    "write",
)
_HIDDEN_WORD = re.compile(rf"(?<!\w)(?:{'|'.join(HIDDEN)})s?(?!\w)", re.IGNORECASE)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _NoBackend:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"backend touched: {name}")


NO_BACKEND = cast("OpikReadClient", _NoBackend())


async def _refusal(call: Awaitable[None], echoed: str = "") -> str:
    with pytest.raises((ToolError, catalog.UnknownSkillError)) as exc:
        await call
    return (
        str(exc.value).replace(repr(echoed), "").replace(echoed, "") if echoed else str(exc.value)
    )


async def _read(entity_type: str, record_id: str = "x", *, since: str | None = None) -> None:
    await run_read(entity_type, record_id, since=since, settings=SPEND, client=NO_BACKEND)


async def _list(
    entity_type: str,
    *,
    since: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    search: str | None = None,
    dataset_id: str | None = None,
    prompt_id: str | None = None,
    experiment_ids: list[str] | None = None,
    status: str | None = None,
) -> None:
    await run_list(
        entity_type,
        since=since,
        filters=filters,
        sort=sort,
        search=search,
        dataset_id=dataset_id,
        prompt_id=prompt_id,
        experiment_ids=experiment_ids,
        status=status,
        settings=SPEND,
        client=NO_BACKEND,
    )


async def _schema(key: str) -> None:
    run_schema(key, settings=SPEND)


async def _skill(name: str) -> None:
    catalog.run_read_skill(name, COST_INTELLIGENCE_MODE)


_CALLS: list[tuple[str, Callable[[], Awaitable[None]]]] = [
    ("nope", lambda: _read("nope")),
    ("dataset", lambda: _read("dataset")),
    ("datasets", lambda: _read("datasets")),
    ("experiment", lambda: _read("experiment", UUID)),
    ("agent_insights_issue", lambda: _read("agent_insights_issue", UUID)),
    ("since", lambda: _read("trace", "t", since="1h")),
    ("since", lambda: _read("thread", "t", since="1h")),
    ("bad-window", lambda: _list("trace", since="garbage")),
    ("since-on-project", lambda: _list("project", since="1d")),
    ("filter", lambda: _list("project", filters="name = 'x'")),
    ("sort", lambda: _list("project", sort="bogus desc")),
    ("search", lambda: _list("project", search="x")),
    ("prompt", lambda: _list("prompt")),
    ("dataset_id", lambda: _list("trace", dataset_id=UUID)),
    ("prompt_id", lambda: _list("trace", prompt_id=UUID)),
    ("experiment_ids", lambda: _list("trace", experiment_ids=[UUID])),
    ("status", lambda: _list("trace", status="open")),
    ("update_thread", lambda: _schema("update_thread")),
    ("list.dataset", lambda: _schema("list.dataset")),
    ("nope", lambda: _schema("nope")),
    ("opik-diagnose", lambda: _skill("opik-diagnose")),
    ("opik-instrument", lambda: _skill("opik-instrument")),
    ("nope", lambda: _skill("nope")),
]


@pytest.mark.parametrize(("echoed", "make_call"), _CALLS)
async def test_a_refusal_in_the_mode_names_no_hidden_type(
    echoed: str, make_call: Callable[[], Awaitable[None]]
) -> None:
    text = await _refusal(make_call(), echoed)
    assert not _HIDDEN_WORD.search(text), text


async def test_a_window_refusal_on_read_names_the_modes_types_only() -> None:
    text = await _refusal(_read("trace", since="1h"))
    assert text.endswith("on read a window is taken by: project, spend_lane, spend_session.")


async def test_a_window_refusal_on_read_is_what_it_was_in_the_default_mode() -> None:
    with pytest.raises(ToolError) as exc:
        await run_read("trace", "t", since="1h", settings=DEFAULT, client=NO_BACKEND)
    assert str(exc.value) == (
        "since/until are not supported for read('trace'); "
        "on read a window is taken by: agent_insights_issue, project."
    )


_HIDDEN_ARGS: list[tuple[str, Callable[[], Awaitable[None]]]] = [
    ("dataset_id", lambda: _list("trace", dataset_id=UUID)),
    ("prompt_id", lambda: _list("trace", prompt_id=UUID)),
    ("experiment_ids", lambda: _list("trace", experiment_ids=[UUID])),
    ("status", lambda: _list("trace", status="open")),
]


@pytest.mark.parametrize(("arg", "make_call"), _HIDDEN_ARGS)
async def test_a_list_argument_the_mode_hides_is_refused_in_one_line(
    arg: str, make_call: Callable[[], Awaitable[None]]
) -> None:
    text = await _refusal(make_call())
    assert text == f"`{arg}` is not available in this workspace."

from __future__ import annotations

import json
from typing import TypedDict

import httpx
import pytest
import respx

from opik_mcp.client.ai_spend import SpendAdminRequiredError, SpendItemKind
from opik_mcp.client.base import (
    OpikAuthError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.client.opik import OpikClient

OPIK_BASE = "https://opik.test"
SPEND = "/v1/private/ai-spend"


class _Window(TypedDict):
    project_name: str
    interval_start: str
    interval_end: str


WINDOW = _Window(
    project_name="p",
    interval_start="2026-09-01T00:00:00Z",
    interval_end="2026-09-08T00:00:00Z",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


def _body(route: respx.Route) -> dict[str, object]:
    body: dict[str, object] = json.loads(route.calls.last.request.read())
    return body


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    ["/summary", "/composition", "/agents"],
)
async def test_window_reads_post_the_window_and_drop_none(path: str) -> None:
    method = {
        "/summary": _client().get_spend_summary,
        "/composition": _client().get_spend_composition,
        "/agents": _client().get_spend_agents,
    }[path]
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}{path}").mock(return_value=httpx.Response(200, json={"a": 1}))
        assert await method(**WINDOW) == {"a": 1}
        assert _body(route) == WINDOW
        await method(**WINDOW, user_email="a@b.c")
        assert _body(route) == {**WINDOW, "user_email": "a@b.c"}
    assert route.calls.last.request.headers["comet-workspace"] == "ws"


@pytest.mark.anyio
async def test_lane_breakdown_quotes_the_lane_key() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}/composition/a%2Fb%20c/breakdown").mock(
            return_value=httpx.Response(200, json={})
        )
        await _client().get_spend_lane_breakdown("a/b c", **WINDOW)
    assert _body(route) == WINDOW


@pytest.mark.anyio
async def test_users_sends_paging_name_and_sorting_as_query() -> None:
    sorting = '[{"field":"total_tokens","direction":"DESC"}]'
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}/users").mock(
            return_value=httpx.Response(200, json={"content": []})
        )
        await _client().list_spend_users(**WINDOW, page=2, size=5, name="ann", sorting=sorting)
    query = route.calls.last.request.url.params
    assert query["page"] == "2"
    assert query["size"] == "5"
    assert query["name"] == "ann"
    assert query["sorting"] == sorting
    assert _body(route) == WINDOW


@pytest.mark.anyio
async def test_users_omits_unset_query_params() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}/users").mock(return_value=httpx.Response(200, json={}))
        await _client().list_spend_users(**WINDOW)
    assert dict(route.calls.last.request.url.params) == {"page": "1", "size": "10"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "route_path", "param"),
    [
        ("mcp_server", "mcp-servers", "server"),
        ("skill", "skills", "skill"),
        ("built_in_tool", "built-in-tools", "tool"),
    ],
)
async def test_item_users_parse_a_top_level_array(
    kind: SpendItemKind, route_path: str, param: str
) -> None:
    rows = [{"user": "a"}, {"user": "b"}]
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}/{route_path}/users").mock(
            return_value=httpx.Response(200, json=rows)
        )
        result = await _client().list_spend_item_users(kind, "x y", **WINDOW)
    assert result == rows
    assert route.calls.last.request.url.params[param] == "x y"
    assert _body(route) == WINDOW


@pytest.mark.anyio
async def test_item_users_reject_an_object_answer() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{SPEND}/skills/users").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(OpikServerError, match="non-array"):
            await _client().list_spend_item_users("skill", "s", **WINDOW)


@pytest.mark.anyio
async def test_sessions_send_query_and_never_a_user_email() -> None:
    filters = '[{"field":"user_email","operator":"=","value":"a@b"}]'
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}/sessions").mock(
            return_value=httpx.Response(200, json={"content": []})
        )
        await _client().list_spend_sessions(
            **WINDOW, page=3, size=20, filters=filters, sorting="[]", search="foo"
        )
    assert dict(route.calls.last.request.url.params) == {
        "page": "3",
        "size": "20",
        "filters": filters,
        "sorting": "[]",
        "search": "foo",
    }
    assert _body(route) == WINDOW


@pytest.mark.anyio
async def test_session_narrative_quotes_id_and_window_is_optional() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"{SPEND}/sessions/s%2F1/narrative").mock(
            return_value=httpx.Response(200, json={"n": 1})
        )
        result = await _client().get_spend_session_narrative("s/1", project_name="p")
    assert result == {"n": 1}
    assert _body(route) == {"project_name": "p"}


@pytest.mark.anyio
async def test_403_becomes_spend_admin_required_error() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{SPEND}/summary").mock(return_value=httpx.Response(403))
        mock.post(f"{SPEND}/skills/users").mock(return_value=httpx.Response(403))
        for call in (
            _client().get_spend_summary(**WINDOW),
            _client().list_spend_item_users("skill", "s", **WINDOW),
        ):
            with pytest.raises(SpendAdminRequiredError) as caught:
                await call
            err = caught.value
            assert isinstance(err, OpikPermissionError)
            assert err.error_kind == "permission"
            assert err.http_status == 403
            assert "organization admin's API key" in str(err)


@pytest.mark.anyio
async def test_401_stays_the_auth_error() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{SPEND}/summary").mock(return_value=httpx.Response(401))
        with pytest.raises(OpikAuthError) as caught:
            await _client().get_spend_summary(**WINDOW)
    assert not isinstance(caught.value, OpikPermissionError)


@pytest.mark.anyio
async def test_400_is_a_validation_error() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{SPEND}/composition/nope/breakdown").mock(return_value=httpx.Response(400))
        with pytest.raises(OpikValidationError):
            await _client().get_spend_lane_breakdown("nope", **WINDOW)


@pytest.mark.anyio
async def test_post_json_sends_params_as_query_string() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/x").mock(return_value=httpx.Response(200, json={}))
        await _client()._post_json("/x", json={"k": 1}, params={"a": "b"}, entity_hint="x")
    assert route.calls.last.request.url.params["a"] == "b"
    assert _body(route) == {"k": 1}


@pytest.mark.anyio
@pytest.mark.parametrize("segment", ["", ".", "..", "...", 'a"b'])
async def test_a_path_segment_that_could_reroute_or_break_oql_is_refused(segment: str) -> None:
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        route = mock.post(url__regex=".*").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(OpikValidationError, match="is not a valid id"):
            await _client().get_spend_session_narrative(segment, project_name="p")
        with pytest.raises(OpikValidationError, match="is not a valid id"):
            await _client().get_spend_lane_breakdown(segment, **WINDOW)
    assert not route.called

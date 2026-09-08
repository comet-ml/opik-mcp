"""Search-parameter coverage for the four searchable list endpoints.

The ``list`` tool's filter / sort / window / search surface rides these query
params. Each client method must forward a param only when set — the backend
treats an empty ``filters=`` as malformed JSON and 400s — and ``list_spans``
must work project-wide, without a ``trace_id``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from opik_mcp.opik_client import OpikClient

OPIK_BASE = "https://opik.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


def _page(items: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "content": items,
        "page": 1,
        "size": len(items),
        "total": total if total is not None else len(items),
    }


_SEARCH_PARAMS: dict[str, Any] = {
    "filters": '[{"field":"error_info","operator":"is_not_empty","key":"","value":""}]',
    "sorting": '[{"field":"duration","direction":"DESC"}]',
    "search": "order-42",
    "from_time": "2026-09-08T00:00:00Z",
    "to_time": "2026-09-08T12:00:00Z",
    "truncate": True,
}


@pytest.mark.parametrize(
    ("method", "path", "scope"),
    [
        ("list_traces", "/v1/private/traces", {"project_id": "p-1"}),
        ("list_spans", "/v1/private/spans", {"project_id": "p-1"}),
        ("list_threads", "/v1/private/traces/threads", {"project_id": "p-1"}),
        ("list_experiments", "/v1/private/experiments", {}),
    ],
)
@pytest.mark.anyio
async def test_list_forwards_search_params_only_when_set(
    method: str, path: str, scope: dict[str, str]
) -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get(path).mock(return_value=httpx.Response(200, json=_page([])))
        await getattr(_client(), method)(**scope)
        bare = dict(route.calls.last.request.url.params)
        for key in _SEARCH_PARAMS:
            assert key not in bare, f"{method} sent {key} without being asked"

        await getattr(_client(), method)(**scope, **_SEARCH_PARAMS)
    params = dict(route.calls.last.request.url.params)
    assert params["filters"] == _SEARCH_PARAMS["filters"]
    assert params["sorting"] == _SEARCH_PARAMS["sorting"]
    assert params["search"] == "order-42"
    assert params["from_time"] == "2026-09-08T00:00:00Z"
    assert params["to_time"] == "2026-09-08T12:00:00Z"
    assert params["truncate"] == "true"


@pytest.mark.anyio
async def test_list_spans_across_a_project_without_trace_id() -> None:
    """Project-wide span search: ``trace_id`` is optional on ``GET /spans``."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/spans").mock(
            return_value=httpx.Response(200, json=_page([{"id": "sp-1"}], total=1)),
        )
        body = await _client().list_spans(project_name="demo", page=2, size=50)
    params = dict(route.calls.last.request.url.params)
    assert params == {"project_name": "demo", "page": "2", "size": "50"}
    assert body["content"][0]["id"] == "sp-1"

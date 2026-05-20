"""GET-side coverage for ``OpikClient`` — backs the resource layer.

Same auth/error contract as the write tests (test_opik_client.py); these
add URL+query+envelope assertions for the 11 read endpoints. Spring Page
envelope is passed through verbatim — normalization to MCP's canonical
``{items,nextCursor?,total?}`` shape lives in ``resources.py``.
"""

import httpx
import pytest
import respx

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikClient,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)

OPIK_BASE = "https://opik.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


def _page(content: list[dict[str, object]], *, page: int = 1, total: int = 0) -> dict[str, object]:
    return {"content": content, "page": page, "size": len(content), "total": total}


# --- projects ------------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_projects_sends_get_with_paging_and_headers() -> None:
    payload = _page([{"id": "p-1", "name": "demo"}], total=1)
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/projects").mock(
            return_value=httpx.Response(200, json=payload),
        )
        body = await _client().list_projects(page=2, size=25)

    req = route.calls.last.request
    assert req.headers["authorization"] == "key-abc"
    assert req.headers["comet-workspace"] == "ws"
    assert dict(req.url.params) == {"page": "2", "size": "25"}
    assert body == payload


@pytest.mark.anyio
async def test_get_project_hits_singleton_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects/p-1").mock(
            return_value=httpx.Response(200, json={"id": "p-1", "name": "demo"}),
        )
        body = await _client().get_project("p-1")
    assert body == {"id": "p-1", "name": "demo"}


# --- traces / spans ------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_traces_requires_project_id_or_name() -> None:
    with pytest.raises(ValueError, match="project_id or project_name"):
        await _client().list_traces()


@pytest.mark.anyio
async def test_list_traces_by_project_id_passes_query_param() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/traces").mock(
            return_value=httpx.Response(200, json=_page([])),
        )
        await _client().list_traces(project_id="p-1", page=1, size=10)
    params = dict(route.calls.last.request.url.params)
    assert params == {"project_id": "p-1", "page": "1", "size": "10"}


@pytest.mark.anyio
async def test_list_traces_by_project_name_passes_query_param() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/traces").mock(
            return_value=httpx.Response(200, json=_page([])),
        )
        await _client().list_traces(project_name="demo")
    assert dict(route.calls.last.request.url.params).get("project_name") == "demo"


@pytest.mark.anyio
async def test_get_trace_hits_singleton_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/traces/tr-1").mock(
            return_value=httpx.Response(200, json={"id": "tr-1", "name": "x"}),
        )
        body = await _client().get_trace("tr-1")
    assert body["id"] == "tr-1"


@pytest.mark.anyio
async def test_list_spans_requires_project_id_or_name() -> None:
    """opik-backend rejects ``GET /spans`` without a project filter (400)."""
    with pytest.raises(ValueError, match="project_id or project_name"):
        await _client().list_spans(trace_id="tr-1")


@pytest.mark.anyio
async def test_list_spans_filters_by_trace_id_and_project_id() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/spans").mock(
            return_value=httpx.Response(200, json=_page([{"id": "sp-1"}])),
        )
        await _client().list_spans(trace_id="tr-1", project_id="p-1")
    params = dict(route.calls.last.request.url.params)
    assert params["trace_id"] == "tr-1"
    assert params["project_id"] == "p-1"


@pytest.mark.anyio
async def test_list_spans_filters_by_trace_id_and_project_name() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/spans").mock(
            return_value=httpx.Response(200, json=_page([{"id": "sp-1"}])),
        )
        await _client().list_spans(trace_id="tr-1", project_name="demo")
    params = dict(route.calls.last.request.url.params)
    assert params["trace_id"] == "tr-1"
    assert params["project_name"] == "demo"


@pytest.mark.anyio
async def test_get_span_hits_singleton_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/spans/sp-1").mock(
            return_value=httpx.Response(200, json={"id": "sp-1"}),
        )
        body = await _client().get_span("sp-1")
    assert body == {"id": "sp-1"}


# --- test suites (REST = datasets) --------------------------------------- #


@pytest.mark.anyio
async def test_get_test_suite_maps_to_dataset_path() -> None:
    """Opik 2.0 test_suite REST path == datasets — assert headers + parsed body.

    `route.called` alone passes if the dataset path is swapped for any other
    endpoint that ds-1 also resolves on; checking the parsed body forces the
    GET to actually return through `_get_json`.
    """
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/datasets/ds-1").mock(
            return_value=httpx.Response(200, json={"id": "ds-1", "name": "suite"}),
        )
        body = await _client().get_test_suite("ds-1")

    req = route.calls.last.request
    assert req.headers["authorization"] == "key-abc"
    assert req.headers["comet-workspace"] == "ws"
    assert body == {"id": "ds-1", "name": "suite"}


@pytest.mark.anyio
async def test_list_test_suite_items_uses_dataset_items_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/datasets/ds-1/items").mock(
            return_value=httpx.Response(200, json=_page([])),
        )
        await _client().list_test_suite_items("ds-1", page=3, size=5)
    params = dict(route.calls.last.request.url.params)
    assert params == {"page": "3", "size": "5"}


# --- experiments ---------------------------------------------------------- #


@pytest.mark.anyio
async def test_get_experiment_hits_singleton_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/experiments/ex-1").mock(
            return_value=httpx.Response(200, json={"id": "ex-1"}),
        )
        body = await _client().get_experiment("ex-1")
    assert body == {"id": "ex-1"}


# --- prompts -------------------------------------------------------------- #


@pytest.mark.anyio
async def test_get_prompt_hits_singleton_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/prompts/pr-1").mock(
            return_value=httpx.Response(200, json={"id": "pr-1", "latestVersion": {"v": 3}}),
        )
        body = await _client().get_prompt("pr-1")
    assert body["latestVersion"] == {"v": 3}


@pytest.mark.anyio
async def test_list_prompt_versions_hits_subresource() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get("/v1/private/prompts/pr-1/versions").mock(
            return_value=httpx.Response(200, json=_page([{"v": 1}, {"v": 2}], total=2)),
        )
        body = await _client().list_prompt_versions("pr-1")
    assert route.called
    assert body["total"] == 2


# --- name= filter coverage on the four nameable list endpoints ------------ #


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("list_projects", "/v1/private/projects"),
        ("list_test_suites", "/v1/private/datasets"),
        ("list_experiments", "/v1/private/experiments"),
        ("list_prompts", "/v1/private/prompts"),
    ],
)
@pytest.mark.anyio
async def test_list_name_filter_lands_in_query_params(method: str, path: str) -> None:
    """The `if name is not None: params['name'] = name` branch on each of the
    four nameable list endpoints. Without this test the read tool's
    name-lookup path is silently broken if anyone removes the branch."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get(path).mock(return_value=httpx.Response(200, json=_page([])))
        await getattr(_client(), method)(name="my-search-term")

    params = dict(route.calls.last.request.url.params)
    assert params["name"] == "my-search-term"
    assert params["page"] == "1"
    assert params["size"] == "10"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("list_projects", "/v1/private/projects"),
        ("list_test_suites", "/v1/private/datasets"),
        ("list_experiments", "/v1/private/experiments"),
        ("list_prompts", "/v1/private/prompts"),
    ],
)
@pytest.mark.anyio
async def test_list_name_filter_omitted_when_none(method: str, path: str) -> None:
    """Negative branch: `name=None` must NOT inject `name=` into the URL.

    A naive `params["name"] = name` (no None check) sends `?name=None` which
    opik-backend's substring filter would treat as "find items containing
    'None'" — a subtly wrong list."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.get(path).mock(return_value=httpx.Response(200, json=_page([])))
        await getattr(_client(), method)()

    params = dict(route.calls.last.request.url.params)
    assert "name" not in params


# --- error mapping (shared with write path) ------------------------------- #


@pytest.mark.parametrize(
    ("status", "expected_exc"),
    [
        (401, OpikAuthError),
        (403, OpikAuthError),
        (404, OpikNotFoundError),
        (400, OpikValidationError),
        (422, OpikValidationError),
        (500, OpikServerError),
        (503, OpikServerError),
    ],
)
@pytest.mark.anyio
async def test_get_maps_status_to_typed_error(status: int, expected_exc: type[Exception]) -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects/p-x").mock(
            return_value=httpx.Response(status, json={"message": "bad"}),
        )
        with pytest.raises(expected_exc):
            await _client().get_project("p-x")


@pytest.mark.anyio
async def test_non_json_response_surfaces_as_server_error() -> None:
    """A 200 with HTML body (e.g. behind a misrouted proxy) is a real failure."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects/p-1").mock(
            return_value=httpx.Response(200, text="<html>oops</html>"),
        )
        with pytest.raises(OpikServerError, match="non-JSON"):
            await _client().get_project("p-1")


@pytest.mark.anyio
async def test_non_object_json_surfaces_as_server_error() -> None:
    """Backend contract is ``object`` for every endpoint; arrays are a bug."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects/p-1").mock(
            return_value=httpx.Response(200, json=[1, 2, 3]),
        )
        with pytest.raises(OpikServerError, match="non-object"):
            await _client().get_project("p-1")

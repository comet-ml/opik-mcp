"""A ``name`` reaches only the list endpoints that match names.

The list functions are called through ``ListFn``, which is ``Callable[..., …]``,
so mypy cannot see a kwarg the endpoint has no parameter for. The real client
has the real signatures, so it is what this goes through.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from opik_mcp.client.opik import OpikClient
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.registry import ENTITY_REGISTRY

OPIK_BASE = "https://opik.test"
PARENT_ID = "11111111-2222-4333-8444-555555555555"

NAMELESS = sorted(
    entity_type
    for entity_type, handler in ENTITY_REGISTRY.items()
    if handler.list_fn is not None and not handler.is_name_searchable
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_the_guard_covers_the_project_scoped_lists() -> None:
    assert {"trace", "span", "thread"} <= set(NAMELESS), NAMELESS


@pytest.mark.anyio
@pytest.mark.parametrize("entity_type", NAMELESS)
async def test_a_name_never_reaches_an_endpoint_that_does_not_match_names(
    entity_type: str,
) -> None:
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        mock.route().mock(
            return_value=httpx.Response(200, json={"content": [], "page": 1, "size": 0, "total": 0})
        )
        await run_list(
            entity_type,
            name="x",
            client=OpikClient(base_url=OPIK_BASE, api_key="key", workspace="ws"),
            # Every parent a list can require; each is sent only where declared.
            project_id=PARENT_ID,
            dataset_id=PARENT_ID,
            prompt_id=PARENT_ID,
        )
        requests = [call.request for call in mock.calls]
    assert requests, f"list({entity_type!r}) sent no request"
    sent_name = [str(request.url) for request in requests if "name" in request.url.params]
    assert not sent_name, f"list({entity_type!r}) sent name= to {sent_name}"

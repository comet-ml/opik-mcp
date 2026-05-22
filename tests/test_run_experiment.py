from uuid import UUID

import httpx
import pytest
import respx

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikClient,
    OpikNotFoundError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.run_experiment import run_experiment_impl
from opik_mcp.run_experiment_models import PromptVariant, RunExperimentConfig

OPIK_BASE = "https://opik.test"
DATASET_ID = UUID("0193a300-0000-7000-8000-000000000123")
EXP_ID = "0193a300-0000-7000-8000-0000000000e1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


def _config() -> RunExperimentConfig:
    return RunExperimentConfig(
        dataset_name="suite-a",
        dataset_id=DATASET_ID,
        prompts=[PromptVariant(model="gpt-4o", messages=[{"role": "user", "content": "Hi"}])],
    )


@pytest.mark.anyio
async def test_run_experiment_impl_returns_ids_immediately() -> None:
    """No polling: tool returns as soon as backend accepts the request."""
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        post_route = mock.post("/v1/private/experiments/execute").mock(
            return_value=httpx.Response(
                202,
                json={
                    "experiments": [{"experiment_id": EXP_ID, "prompt_index": 0}],
                    "total_items": 200,
                },
            )
        )
        # Guard: orchestrator must NEVER call GET /experiments/{id}.
        get_route = mock.get(f"/v1/private/experiments/{EXP_ID}").mock(
            return_value=httpx.Response(500)
        )

        result = await run_experiment_impl(
            config=_config(),
            client=_client(),
            comet_base_url="https://www.comet.com",
            workspace="ws",
        )

    assert result.experiment_ids == [EXP_ID]
    assert result.prompt_indexes == [0]
    assert result.total_items == 200
    # Brackets are percent-encoded so the URL stays RFC 3986 compliant.
    assert result.summary_url == (
        f"https://www.comet.com/ws/redirect/experiments?experiments=%5B{EXP_ID}%5D"
    )
    assert post_route.call_count == 1
    assert get_route.call_count == 0


@pytest.mark.anyio
async def test_run_experiment_impl_maps_404_to_not_found() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/experiments/execute").mock(
            return_value=httpx.Response(404, json={"message": "dataset not found"})
        )
        with pytest.raises(OpikNotFoundError):
            await run_experiment_impl(
                config=_config(),
                client=_client(),
                comet_base_url="https://www.comet.com",
                workspace="ws",
            )


@pytest.mark.anyio
async def test_run_experiment_impl_maps_422_to_validation() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/experiments/execute").mock(
            return_value=httpx.Response(422, json={"message": "invalid prompts"})
        )
        with pytest.raises(OpikValidationError):
            await run_experiment_impl(
                config=_config(),
                client=_client(),
                comet_base_url="https://www.comet.com",
                workspace="ws",
            )


@pytest.mark.anyio
async def test_run_experiment_impl_maps_401_to_auth() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/experiments/execute").mock(return_value=httpx.Response(401))
        with pytest.raises(OpikAuthError):
            await run_experiment_impl(
                config=_config(),
                client=_client(),
                comet_base_url="https://www.comet.com",
                workspace="ws",
            )


@pytest.mark.anyio
async def test_run_experiment_impl_maps_403_to_permission() -> None:
    """403 must surface as OpikPermissionError, not generic OpikAuthError —
    so the analytics layer can bucket it as opik_permission_denied (workspace
    mismatch) rather than opik_auth_failed (bad key)."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/experiments/execute").mock(return_value=httpx.Response(403))
        with pytest.raises(OpikPermissionError):
            await run_experiment_impl(
                config=_config(),
                client=_client(),
                comet_base_url="https://www.comet.com",
                workspace="ws",
            )


@pytest.mark.anyio
async def test_run_experiment_impl_maps_503_to_server() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/experiments/execute").mock(
            return_value=httpx.Response(503, text="upstream down")
        )
        with pytest.raises(OpikServerError):
            await run_experiment_impl(
                config=_config(),
                client=_client(),
                comet_base_url="https://www.comet.com",
                workspace="ws",
            )


@pytest.mark.anyio
async def test_run_experiment_impl_wraps_non_json_2xx_body_as_server_error() -> None:
    """A 202 with a malformed body surfaces as OpikServerError, not a raw JSONDecodeError."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/experiments/execute").mock(
            return_value=httpx.Response(202, text="not json at all")
        )
        with pytest.raises(OpikServerError, match="non-JSON"):
            await run_experiment_impl(
                config=_config(),
                client=_client(),
                comet_base_url="https://www.comet.com",
                workspace="ws",
            )

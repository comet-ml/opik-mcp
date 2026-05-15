"""Live end-to-end checks against dev.comet.com for score/comment.

Default-skipped — set ``RUN_LIVE_DEV_COMET=1`` to enable. These tests are
deliberately non-destructive: they target a known-missing trace UUID and
assert that we get clean ``OpikNotFoundError`` / ``OpikAuthError`` responses.
That proves auth headers, URL routing, body shape, and error mapping all
work against the real backend without polluting any real traces.

When ``create_trace`` lands as a tool, we'll extend this with a true
round-trip: create a throwaway trace → score it → comment it → verify.
"""

import os
import uuid

import pytest

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikAuthError, OpikClient, OpikNotFoundError
from opik_mcp.score_comment import (
    Target,
    _require_opik_config,
    run_comment,
    run_score,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DEV_COMET") != "1",
    reason="Set RUN_LIVE_DEV_COMET=1 to hit dev.comet.com for real.",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _live_settings() -> Settings:
    s = Settings()
    assert s.opik_api_key, "Set OPIK_API_KEY"
    assert s.comet_workspace, "Set COMET_WORKSPACE"
    return s


@pytest.mark.anyio
async def test_live_score_missing_trace_returns_clean_404() -> None:
    settings = _live_settings()
    target = Target(type="trace", id=str(uuid.uuid4()))
    with pytest.raises(OpikNotFoundError):
        await run_score(
            target=target,
            name="opik-mcp.live-smoke",
            value=0.0,
            settings=settings,
        )


@pytest.mark.anyio
async def test_live_comment_missing_trace_returns_clean_404() -> None:
    settings = _live_settings()
    target = Target(type="trace", id=str(uuid.uuid4()))
    with pytest.raises(OpikNotFoundError):
        await run_comment(
            target=target,
            text="(opik-mcp live smoke — should never land)",
            settings=settings,
        )


@pytest.mark.anyio
async def test_live_bad_api_key_returns_clean_auth_error() -> None:
    """Auth failures surface as OpikAuthError, not raw httpx.HTTPStatusError."""
    base_url, _, workspace = _require_opik_config(_live_settings())
    client = OpikClient(base_url=base_url, api_key="not-a-real-key", workspace=workspace)
    with pytest.raises(OpikAuthError):
        await run_comment(
            target=Target(type="trace", id=str(uuid.uuid4())),
            text="should fail before reaching the entity check",
            settings=_live_settings(),
            client=client,
        )

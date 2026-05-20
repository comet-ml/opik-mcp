import os

import pytest

from opik_mcp.ask_ollie import run_ask_ollie
from opik_mcp.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DEV_COMET") != "1",
    reason="Set RUN_LIVE_DEV_COMET=1 to hit dev.comet.com for real.",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_live_ask_ollie_against_dev_comet() -> None:
    settings = Settings()
    assert settings.opik_api_key, "Set OPIK_API_KEY"
    assert settings.comet_workspace, "Set COMET_WORKSPACE"

    result = await run_ask_ollie(
        query="Reply with the single word: pong.",
        settings=settings,
    )

    assert result.text.strip(), "expected non-empty response"
    assert result.thread_id, "expected a thread_id from message_end"


@pytest.mark.anyio
async def test_ask_ollie_without_project_returns_workspace_wide_answer() -> None:
    """No project context — Ollie should still respond, scoped to the workspace.

    Regression guard: an early concern was that Ollie would reject calls with
    no `context.project_id` / `context.project_name`. It doesn't — meta queries
    ("how many projects do I have?") and authoring help work fine without one.
    """
    settings = Settings()
    assert settings.opik_api_key, "Set OPIK_API_KEY"
    assert settings.comet_workspace, "Set COMET_WORKSPACE"

    result = await run_ask_ollie(
        query="How many projects exist in this workspace? Answer with a number.",
        settings=settings,
    )

    assert result.text.strip(), "expected non-empty response with no project context"
    assert result.thread_id

import pytest

from opik_mcp import server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_run_experiment_tool_is_registered() -> None:
    """Surface guard: the tool must be in the FastMCP registry."""
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert "run_experiment" in names


def test_run_experiment_tool_description_mentions_test_suite_and_read() -> None:
    """Description should explain that experiments are async and point at `read`."""
    tool = next(t for t in server.mcp._tool_manager.list_tools() if t.name == "run_experiment")
    desc = (tool.description or "").lower()
    assert "test suite" in desc or "evaluation_suite" in desc
    # Async semantics — caller should know to use `read` for progress.
    assert "read" in desc


def test_run_experiment_tool_description_does_not_push_link_to_user() -> None:
    """Description must not instruct the LLM to surface the URL to the end user.

    The summary_url is structured data on the result; what the host does with
    it is the host's call. The tool description should describe mechanics, not
    UX guidance.
    """
    tool = next(t for t in server.mcp._tool_manager.list_tools() if t.name == "run_experiment")
    desc = (tool.description or "").lower()
    forbidden = ["navigate the user", "deep-link the user", "show the user", "tell the user"]
    assert not any(p in desc for p in forbidden), f"description leaks UX guidance: {desc}"


@pytest.mark.anyio
async def test_run_experiment_tool_callable_via_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end-shape check: tool function accepts the documented kwargs."""
    monkeypatch.setenv("OPIK_API_KEY", "key-abc")
    monkeypatch.setenv("COMET_WORKSPACE", "ws")
    # get_settings is lru_cached; clear so the env vars above are picked up.
    from opik_mcp.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, object] = {}

    async def fake_impl(**kwargs: object) -> object:
        captured.update(kwargs)
        from opik_mcp.run_experiment_models import RunExperimentResult

        return RunExperimentResult(
            experiment_ids=["0193a300-0000-7000-8000-0000000000e1"],
            prompt_indexes=[0],
            total_items=3,
            summary_url="https://www.comet.com/ws/redirect/experiments?experiments=%5Be1%5D",
        )

    monkeypatch.setattr(server, "run_experiment_impl", fake_impl)
    result = await server.run_experiment(
        experiment_config={
            "dataset_name": "suite-a",
            "dataset_id": "0193a300-0000-7000-8000-000000000123",
            "prompts": [{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]}],
        },
    )
    assert result.experiment_ids == ["0193a300-0000-7000-8000-0000000000e1"]
    assert result.total_items == 3
    # The orchestrator was called with no polling kwargs:
    assert "wait_for_completion" not in captured
    assert "poll_interval" not in captured
    # Workspace must propagate from settings.
    assert captured.get("workspace") == "ws"
    get_settings.cache_clear()

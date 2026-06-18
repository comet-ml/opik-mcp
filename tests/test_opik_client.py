import httpx
import pytest
import respx

from opik_mcp.opik_client import (
    FeedbackScore,
    OpikAuthError,
    OpikClient,
    OpikNotFoundError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)

OPIK_BASE = "https://opik.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


# --- happy paths: feedback scores ---------------------------------------- #


@pytest.mark.anyio
async def test_trace_feedback_score_sends_put_with_required_fields() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/traces/tr-1/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await _client().add_trace_feedback_score(
            "tr-1", FeedbackScore(name="helpfulness", value=0.8)
        )

    req = route.calls.last.request
    assert req.headers["authorization"] == "key-abc"
    assert req.headers["comet-workspace"] == "ws"
    assert req.headers["content-type"].startswith("application/json")
    assert req.read() == b'{"name":"helpfulness","value":0.8,"source":"sdk"}'


@pytest.mark.anyio
async def test_trace_feedback_score_omits_none_optional_fields() -> None:
    """Optional category_name / reason must not appear in the body when None."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/traces/tr-1/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await _client().add_trace_feedback_score("tr-1", FeedbackScore(name="x", value=1.0))

    body = route.calls.last.request.read()
    assert b"category_name" not in body
    assert b"reason" not in body


@pytest.mark.anyio
async def test_trace_feedback_score_includes_optional_fields_when_set() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/traces/tr-1/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await _client().add_trace_feedback_score(
            "tr-1",
            FeedbackScore(
                name="quality",
                value=0.5,
                category_name="manual",
                reason="user-confirmed",
            ),
        )

    body = route.calls.last.request.read()
    assert b'"category_name":"manual"' in body
    assert b'"reason":"user-confirmed"' in body


@pytest.mark.anyio
async def test_span_feedback_score_sends_put_with_required_fields() -> None:
    """Mirror of the trace-feedback shape test on the span endpoint.

    Asserts the actual bytes — a `route.called`-only assertion silently passes
    if a refactor swaps the trace/span URLs or drops a required field.
    """
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/spans/sp-1/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await _client().add_span_feedback_score(
            "sp-1", FeedbackScore(name="helpfulness", value=0.8)
        )

    req = route.calls.last.request
    assert req.headers["authorization"] == "key-abc"
    assert req.headers["comet-workspace"] == "ws"
    assert req.read() == b'{"name":"helpfulness","value":0.8,"source":"sdk"}'


@pytest.mark.anyio
async def test_thread_feedback_score_wraps_in_batch_envelope() -> None:
    """Thread scoring is batch-only. Single MCP score → 1-element scores array."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/traces/threads/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await _client().add_thread_feedback_score("th-1", FeedbackScore(name="x", value=0.9))

    body = route.calls.last.request.read()
    assert body == b'{"scores":[{"thread_id":"th-1","name":"x","value":0.9,"source":"sdk"}]}'


@pytest.mark.anyio
async def test_thread_feedback_score_with_project_name_passthrough() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/traces/threads/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await _client().add_thread_feedback_score(
            "th-1",
            FeedbackScore(name="x", value=1.0),
            project_name="demo",
        )

    body = route.calls.last.request.read()
    assert b'"project_name":"demo"' in body


# --- happy paths: comments ----------------------------------------------- #


@pytest.mark.anyio
async def test_trace_comment_posts_text() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/traces/tr-1/comments").mock(
            return_value=httpx.Response(
                201, headers={"Location": "/v1/private/traces/tr-1/comments/c-1"}
            )
        )
        await _client().add_trace_comment("tr-1", "looks good")

    req = route.calls.last.request
    assert req.headers["comet-workspace"] == "ws"
    assert req.read() == b'{"text":"looks good"}'


@pytest.mark.anyio
async def test_span_comment_posts_text() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/spans/sp-1/comments").mock(return_value=httpx.Response(201))
        await _client().add_span_comment("sp-1", "note")

    req = route.calls.last.request
    assert req.headers["comet-workspace"] == "ws"
    assert req.read() == b'{"text":"note"}'


@pytest.mark.anyio
async def test_thread_comment_uses_thread_id_in_path() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/traces/threads/th-1/comments").mock(
            return_value=httpx.Response(201)
        )
        await _client().add_thread_comment("th-1", "see follow-up")

    assert route.called
    assert route.calls.last.request.read() == b'{"text":"see follow-up"}'


# --- error mapping (uniform across all 6 endpoints) ----------------------- #


@pytest.mark.parametrize(
    ("status", "expected_exc"),
    [
        (401, OpikAuthError),
        (403, OpikPermissionError),
        (404, OpikNotFoundError),
        (400, OpikValidationError),
        (422, OpikValidationError),
        (500, OpikServerError),
        (502, OpikServerError),
        (503, OpikServerError),
    ],
)
@pytest.mark.anyio
async def test_trace_feedback_score_maps_status_to_typed_error(
    status: int, expected_exc: type[Exception]
) -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.put("/v1/private/traces/tr-1/feedback-scores").mock(
            return_value=httpx.Response(status, json={"message": "bad"})
        )
        with pytest.raises(expected_exc):
            await _client().add_trace_feedback_score("tr-1", FeedbackScore(name="x", value=1.0))


@pytest.mark.anyio
async def test_not_found_error_includes_entity_hint() -> None:
    """404 message should make it clear which entity was missing."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.put("/v1/private/spans/sp-missing/feedback-scores").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(OpikNotFoundError, match=r"sp-missing"):
            await _client().add_span_feedback_score(
                "sp-missing", FeedbackScore(name="x", value=1.0)
            )


@pytest.mark.anyio
async def test_write_path_propagates_read_timeout_unchanged() -> None:
    """The write path does NOT translate `httpx.ReadTimeout` to OpikServerError.

    Pinning this behavior so a refactor that wraps transport errors (which
    would be a real semantic change — callers currently use `try/except
    httpx.TimeoutException` directly) can't happen silently.

    A regression that started swallowing the timeout (e.g. inside
    `_raise_for_status`) would surface as `expected_exc` no longer firing.
    """
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.put("/v1/private/traces/tr-1/feedback-scores").mock(
            side_effect=httpx.ReadTimeout("read timed out")
        )
        with pytest.raises(httpx.ReadTimeout):
            await _client().add_trace_feedback_score("tr-1", FeedbackScore(name="x", value=1.0))


@pytest.mark.anyio
async def test_write_path_propagates_connect_timeout_unchanged() -> None:
    """Same contract as ReadTimeout for the comment endpoint — the network
    boundary error surfaces untranslated so callers can decide retry policy."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/traces/tr-1/comments").mock(
            side_effect=httpx.ConnectTimeout("connect timed out")
        )
        with pytest.raises(httpx.ConnectTimeout):
            await _client().add_trace_comment("tr-1", "hello")


@pytest.mark.anyio
async def test_validation_error_includes_server_body_excerpt() -> None:
    """A 400 with a JSON message should surface that message to the caller."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/traces/tr-1/comments").mock(
            return_value=httpx.Response(400, json={"message": "text must be non-blank"})
        )
        with pytest.raises(OpikValidationError, match=r"text must be non-blank"):
            await _client().add_trace_comment("tr-1", "")


# --- client injection (for tests + e.g. shared connection pool) ----------- #


@pytest.mark.anyio
async def test_uses_injected_httpx_client_when_provided() -> None:
    """The orchestrator may want to share one AsyncClient across calls."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/traces/tr-1/comments").mock(return_value=httpx.Response(201))
        async with httpx.AsyncClient() as injected:
            client = OpikClient(
                base_url=OPIK_BASE,
                api_key="k",
                workspace="ws",
                client=injected,
            )
            await client.add_trace_comment("tr-1", "x")

    assert route.called


# --- base URL handling ---------------------------------------------------- #


@pytest.mark.anyio
async def test_base_url_trailing_slash_is_normalized() -> None:
    with respx.mock(base_url="https://opik.test") as mock:
        route = mock.post("/v1/private/traces/tr-1/comments").mock(return_value=httpx.Response(201))
        client = OpikClient(base_url="https://opik.test/", api_key="k", workspace="ws")
        await client.add_trace_comment("tr-1", "x")

    assert route.called


# --- resolve_opik_config -------------------------------------------------- #


def test_resolve_opik_config_uses_opik_url_when_set() -> None:
    from opik_mcp.config import Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key="k", comet_workspace="ws", opik_url="https://opik.example.com/")
    base, _api, _ws = resolve_opik_config(s)
    assert base == "https://opik.example.com"


def test_resolve_opik_config_derives_from_comet_url_override() -> None:
    from opik_mcp.config import Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(
        opik_api_key="k",
        comet_workspace="ws",
        comet_url_override="https://dev.comet.com/",
        opik_url=None,
    )
    base, _, _ = resolve_opik_config(s)
    assert base == "https://dev.comet.com/opik/api"


def test_resolve_opik_config_rejects_empty_url_pair() -> None:
    """``COMET_URL_OVERRIDE=""`` (explicit empty) is a misconfiguration.

    Without this guard, the base URL would become ``/opik/api`` — a relative
    URL — and httpx would target wherever the process happens to be running.
    Better to fail loudly at construction.
    """
    from opik_mcp.config import MissingConfigError, Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key="k", comet_workspace="ws", comet_url_override="", opik_url=None)
    with pytest.raises(MissingConfigError, match="OPIK_URL or COMET_URL_OVERRIDE"):
        resolve_opik_config(s)


def test_resolve_opik_config_requires_api_key() -> None:
    from opik_mcp.config import MissingConfigError, Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key=None, comet_workspace="ws")
    with pytest.raises(MissingConfigError, match="OPIK_API_KEY"):
        resolve_opik_config(s)


def test_resolve_opik_config_defaults_workspace_when_unset() -> None:
    """Workspace is optional — when unset, resolve_opik_config falls back to
    "default" (matching the Opik SDK) instead of raising."""
    from opik_mcp.config import DEFAULT_WORKSPACE, Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key="k", comet_workspace=None, opik_url="https://opik.example.com/")
    _base, _api_key, workspace = resolve_opik_config(s)
    assert workspace == DEFAULT_WORKSPACE


def test_resolve_opik_config_still_requires_api_key() -> None:
    """The api key is still mandatory — only the workspace became optional."""
    from opik_mcp.config import MissingConfigError, Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key=None, comet_workspace="ws")
    with pytest.raises(MissingConfigError, match="OPIK_API_KEY"):
        resolve_opik_config(s)


def test_resolve_opik_config_oauth_token_makes_workspace_optional() -> None:
    from opik_mcp.auth_context import inbound_authorization
    from opik_mcp.config import Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key=None, comet_workspace=None, opik_url="https://opik.example.com")
    token = inbound_authorization.set("Bearer opik_mcp_at_abc123")
    try:
        _base, api_key, workspace = resolve_opik_config(s)
    finally:
        inbound_authorization.reset(token)

    assert api_key == "Bearer opik_mcp_at_abc123"
    assert workspace is None


def test_resolve_opik_config_oauth_detection_is_prefix_not_substring() -> None:
    """A bearer that merely *contains* the OAuth marker mid-string is NOT an
    OAuth token, so it takes the non-OAuth path: the workspace falls back to
    "default" (the OAuth-passthrough path would instead leave it None).
    """
    from opik_mcp.auth_context import inbound_authorization
    from opik_mcp.config import DEFAULT_WORKSPACE, Settings
    from opik_mcp.opik_client import resolve_opik_config

    s = Settings(opik_api_key=None, comet_workspace=None, opik_url="https://opik.example.com")
    token = inbound_authorization.set("Bearer sk-xopik_mcp_at_y")
    try:
        _base, _api_key, workspace = resolve_opik_config(s)
    finally:
        inbound_authorization.reset(token)
    assert workspace == DEFAULT_WORKSPACE


def test_oauth_client_omits_workspace_header() -> None:
    from opik_mcp.opik_client import OpikClient

    client = OpikClient(
        base_url="https://opik.example.com", api_key="Bearer opik_mcp_at_x", workspace=None
    )
    headers = client._headers()
    assert "Comet-Workspace" not in headers
    assert headers["Authorization"] == "Bearer opik_mcp_at_x"


# --- execute_experiment ------------------------------------------------------- #


@pytest.mark.anyio
async def test_execute_experiment_posts_to_execute_endpoint() -> None:
    body = {
        "dataset_name": "suite-a",
        "dataset_id": "0193a300-0000-7000-8000-000000000123",
        "prompts": [
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "configs": {"temperature": 0.0},
            }
        ],
    }
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/experiments/execute").mock(
            return_value=httpx.Response(
                202,
                json={
                    "experiments": [
                        {
                            "experiment_id": "0193a300-0000-7000-8000-0000000000e1",
                            "prompt_index": 0,
                        }
                    ],
                    "total_items": 12,
                },
            )
        )
        resp = await _client().execute_experiment(body)

    assert resp.status_code == 202
    payload = resp.json()
    assert payload["total_items"] == 12
    assert payload["experiments"][0]["prompt_index"] == 0
    sent = route.calls.last.request
    assert sent.headers["comet-workspace"] == "ws"
    # Body is forwarded verbatim:
    assert sent.read().startswith(b'{"dataset_name":"suite-a"')

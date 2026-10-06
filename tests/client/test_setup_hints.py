"""A broken setup explains itself in the first failed call.

The agent sees only the tool error: not the server's env, its log or the URL it
calls. Each test breaks one thing the way a user does and checks that the error
carries what the agent needs to fix it: the URL that was tried, the setting to
change, where to get a key, and that the MCP client must restart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.analytics.errors import bucket_exception
from opik_mcp.client.setup_hints import (
    CLOUD_KEY_PAGE,
    HOSTED_SERVER_URL,
    OPEN_SOURCE_LOCAL_API,
    RESTART,
    url_warnings,
)
from opik_mcp.config import Settings
from opik_mcp.identity.context import inbound_authorization
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.writes.write_tool import run_write
from tests.factories import make_settings

CLOUD_API = "https://www.comet.com/opik/api"
TRACE_ID = "0197a6f0-0000-7000-8000-000000000001"

Call = Callable[[Settings], Awaitable[str]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(opik_url: str, **overrides: object) -> Settings:
    return make_settings(**{"opik_url": opik_url, "opik_api_key": "key-abc", **overrides})


async def _list(settings: Settings) -> str:
    return await run_list("project", settings=settings)


async def _read(settings: Settings) -> str:
    return await run_read("trace", TRACE_ID, settings=settings)


async def _write(settings: Settings) -> str:
    data = {"target": "trace", "target_id": TRACE_ID, "name": "h", "value": 0.5}
    return str(await run_write(operation="score.create", data=data, settings=settings))


TOOLS = pytest.mark.parametrize("call", [_list, _read, _write], ids=["list", "read", "write"])


async def _error(call: Call, settings: Settings) -> ToolError:
    with pytest.raises(ToolError) as raised:
        await call(settings)
    return raised.value


@contextmanager
def _hosted_caller() -> Iterator[None]:
    """A call on a hosted server: the caller's credential, not the server's env."""
    token = inbound_authorization.set("Bearer sk-caller-key")
    try:
        yield
    finally:
        inbound_authorization.reset(token)


# --- a missing API key ---------------------------------------------------- #


@pytest.mark.anyio
@TOOLS
async def test_a_401_with_no_key_on_cloud_says_where_to_get_one(call: Call) -> None:
    settings = _settings(CLOUD_API, opik_api_key=None)
    with respx.mock(base_url=CLOUD_API) as mock:
        mock.route().mock(return_value=httpx.Response(401))
        message = str(await _error(call, settings))

    for needed in ("OPIK_API_KEY", CLOUD_KEY_PAGE, HOSTED_SERVER_URL, RESTART):
        assert needed in message, f"{needed!r} missing from: {message}"


@pytest.mark.anyio
@TOOLS
async def test_a_401_with_a_key_names_the_deployment_the_key_must_come_from(call: Call) -> None:
    with respx.mock(base_url=CLOUD_API) as mock:
        mock.route().mock(return_value=httpx.Response(401))
        message = str(await _error(call, _settings(CLOUD_API)))

    for needed in ("OPIK_API_KEY", "OPIK_WORKSPACE", "https://www.comet.com", RESTART):
        assert needed in message, f"{needed!r} missing from: {message}"
    assert CLOUD_KEY_PAGE not in message, "a key was sent; the problem is not a missing one"


@pytest.mark.anyio
async def test_an_unexpanded_key_placeholder_is_called_out() -> None:
    settings = _settings(CLOUD_API, opik_api_key="${OPIK_API_KEY}")
    with respx.mock(base_url=CLOUD_API) as mock:
        mock.route().mock(return_value=httpx.Response(401))
        message = str(await _error(_list, settings))

    assert "placeholder" in message


@pytest.mark.anyio
async def test_a_hosted_401_keeps_the_plain_credential_hint() -> None:
    with respx.mock(base_url=CLOUD_API) as mock, _hosted_caller():
        mock.route().mock(return_value=httpx.Response(401))
        message = str(await _error(_list, _settings(CLOUD_API)))

    assert message.endswith("Check OPIK_API_KEY and OPIK_WORKSPACE.")


@pytest.mark.anyio
@TOOLS
async def test_a_403_names_the_workspace_the_call_used(call: Call) -> None:
    with respx.mock(base_url=CLOUD_API) as mock:
        mock.route().mock(return_value=httpx.Response(403))
        message = str(await _error(call, _settings(CLOUD_API, comet_workspace="team-a")))

    assert "'team-a'" in message
    assert "OPIK_WORKSPACE" in message


@pytest.mark.anyio
async def test_a_self_hosted_502_says_to_check_the_backend() -> None:
    base = "http://localhost:5173/api"
    with respx.mock(base_url=base) as mock:
        mock.route().mock(return_value=httpx.Response(502, text="Bad Gateway"))
        message = str(await _error(_list, _settings(base)))

    assert "backend is running" in message


@pytest.mark.anyio
async def test_a_401_with_no_key_on_self_hosted_does_not_send_the_user_to_cloud() -> None:
    base = "https://comet.acme.example/opik/api"
    with respx.mock(base_url=base) as mock:
        mock.route().mock(return_value=httpx.Response(401))
        message = str(await _error(_list, _settings(base, opik_api_key=None)))

    assert "OPIK_API_KEY" in message
    assert "www.comet.com" not in message


# --- Opik cannot be reached ----------------------------------------------- #


@pytest.mark.anyio
@TOOLS
async def test_an_unreachable_local_opik_names_the_url_and_the_open_source_api(
    call: Call,
) -> None:
    base = "http://localhost:5173/opik/api"
    with respx.mock(base_url=base) as mock:
        mock.route().mock(side_effect=httpx.ConnectError("All connection attempts failed"))
        error = await _error(call, _settings(base))

    message = str(error)
    for needed in (base, "Is Opik running?", OPEN_SOURCE_LOCAL_API, "OPIK_URL", RESTART):
        assert needed in message, f"{needed!r} missing from: {message}"
    # The text changed; the analytics bucket must not.
    assert bucket_exception(error) == "network"


@pytest.mark.anyio
async def test_an_unreachable_remote_host_names_the_url_and_the_settings() -> None:
    base = "https://opik.acme.example/api"
    with respx.mock(base_url=base) as mock:
        mock.route().mock(side_effect=httpx.ConnectError("nodename nor servname provided"))
        message = str(await _error(_read, _settings(base)))

    for needed in (base, "nodename nor servname provided", "OPIK_URL", "COMET_URL_OVERRIDE"):
        assert needed in message, f"{needed!r} missing from: {message}"


@pytest.mark.anyio
@TOOLS
async def test_a_host_that_never_answers_names_the_url_not_the_call_size(call: Call) -> None:
    """A VPN that is off or a firewall that drops connections times out on connect."""
    base = "https://opik.acme.example/api"
    with respx.mock(base_url=base) as mock:
        mock.route().mock(side_effect=httpx.ConnectTimeout(""))
        message = str(await _error(call, _settings(base)))

    assert base in message
    assert "OPIK_URL" in message
    assert "may have been applied" not in message, "a write that never connected was not sent"


@pytest.mark.anyio
async def test_the_url_in_an_error_never_carries_its_password() -> None:
    base = "https://user:s3cret-pass@opik.acme.example/api"
    with respx.mock() as mock:
        mock.route().mock(side_effect=httpx.ConnectError("refused"))
        message = str(await _error(_list, _settings(base)))

    assert "s3cret-pass" not in message
    assert "https://opik.acme.example/api" in message


@pytest.mark.anyio
async def test_a_hosted_server_names_no_url_and_no_env() -> None:
    """The hosted server's backend URL is not the caller's to change or see."""
    base = "http://opik-backend.internal:8080"
    with respx.mock(base_url=base) as mock, _hosted_caller():
        mock.route().mock(side_effect=httpx.ConnectError("refused"))
        message = str(await _error(_list, _settings(base)))

    assert "opik-backend.internal" not in message
    assert "OPIK_URL" not in message


@pytest.mark.anyio
async def test_a_write_that_timed_out_says_it_may_have_landed() -> None:
    with respx.mock(base_url=CLOUD_API) as mock:
        mock.route().mock(side_effect=httpx.ReadTimeout("slow"))
        message = str(await _error(_write, _settings(CLOUD_API)))

    assert "may have been applied" in message


# --- startup -------------------------------------------------------------- #


def test_a_local_comet_url_override_warns_to_set_opik_url_instead() -> None:
    settings = make_settings(comet_url_override="http://localhost:5173", opik_url=None)
    (warning,) = url_warnings(settings)
    assert "OPIK_URL=http://localhost:5173/api" in warning


@pytest.mark.parametrize(
    "overrides",
    [
        {"comet_url_override": "http://localhost:5173", "opik_url": "http://localhost:5173/api"},
        {"comet_url_override": "https://comet.acme.example", "opik_url": None},
        {"comet_url_override": "https://www.comet.com", "opik_url": None},
    ],
    ids=["opik-url-set", "self-hosted-comet", "cloud"],
)
def test_a_url_setup_that_can_work_logs_no_warning(overrides: dict[str, object]) -> None:
    assert url_warnings(make_settings(**overrides)) == []

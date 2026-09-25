"""The write envelope's 401 carries the same OAuth-expiry hint as the read path.

``BackendError`` is what every write tool returns for a rejected backend call;
its message is the only text the model sees. Under an OAuth bearer a 401 means
the access token died, and the hint has to say so (OPIK-8252) — one source of
truth with the read/list client so the two paths cannot drift.
"""

from collections.abc import Iterator

import pytest

from opik_mcp.auth_context import (
    OAUTH_ACCESS_TOKEN_PREFIX,
    OAUTH_TOKEN_EXPIRED_HINT,
    inbound_authorization,
)
from opik_mcp.writes.errors import BackendError


@pytest.fixture
def _inbound(request: pytest.FixtureRequest) -> Iterator[None]:
    token = inbound_authorization.set(request.param)
    try:
        yield
    finally:
        inbound_authorization.reset(token)


@pytest.mark.parametrize("_inbound", [f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}dead"], indirect=True)
def test_401_under_oauth_bearer_says_token_expired(_inbound: None) -> None:
    err = BackendError.build("score.create", 401, {}, method="PUT", path="/v1/private/x")
    assert OAUTH_TOKEN_EXPIRED_HINT in err.message


@pytest.mark.parametrize("_inbound", ["Bearer some-static-api-key"], indirect=True)
def test_401_under_api_key_keeps_the_bare_message(_inbound: None) -> None:
    err = BackendError.build("score.create", 401, {}, method="PUT", path="/v1/private/x")
    assert err.message == "Backend rejected PUT /v1/private/x with status 401."


@pytest.mark.parametrize("_inbound", [f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}live"], indirect=True)
def test_non_401_never_carries_the_hint(_inbound: None) -> None:
    err = BackendError.build("score.create", 403, {}, method="PUT", path="/v1/private/x")
    assert OAUTH_TOKEN_EXPIRED_HINT not in err.message

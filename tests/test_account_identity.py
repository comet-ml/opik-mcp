"""Identity resolution for API-key installs.

The behaviour that matters is not "does it fetch" — it is that a user never
waits for it, a deployment that cannot answer is never asked, and every failure
lands on "we don't know" rather than on an exception or a wrong answer.

Every test runs against a throwaway HOME so the developer's real cache is never
read or written.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from opik_mcp.account_identity import (
    CACHE_TTL_SECONDS,
    reset_account_identity_for_tests,
    resolve_api_key_identity,
)
from opik_mcp.config import Settings
from opik_mcp.session_identity import credential_digest, reset_identities_for_tests

API_KEY = "sk-test-key"
ACCOUNT_URL = "https://www.comet.com/api/rest/v2/account-details"


@pytest.fixture(autouse=True)
def _fresh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    reset_identities_for_tests()
    reset_account_identity_for_tests()
    yield tmp_path
    reset_identities_for_tests()
    reset_account_identity_for_tests()


def _cloud_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(opik_api_key=API_KEY, opik_url="https://www.comet.com/opik/api")
    return Settings(**{**base, **overrides})


def _cache_file(home: Path) -> Path:
    return home / ".opik-mcp" / "identity-cache.json"


def _write_cache(
    home: Path, *, key: str = API_KEY, age_seconds: float = 0.0, **fields: Any
) -> None:
    path = _cache_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"user_name": "cached-user", "workspace_name": "cached-ws", **fields}
    entry["cached_at"] = time.time() - age_seconds
    path.write_text(json.dumps({credential_digest(key): entry}))


def _await_resolution(settings: Settings, timeout_s: float = 3.0) -> Any:
    """Poll until the background refresh lands, or give up.

    Resolution is deliberately asynchronous, so a test that asserts the *result*
    has to wait for it. A test asserting the *absence* of a call must not use
    this — it would pass for the wrong reason.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        identity = resolve_api_key_identity(settings)
        if identity is not None:
            return identity
        time.sleep(0.02)
    return None


# --- the endpoint is only called where it can answer --------------------- #


@respx.mock
def test_self_hosted_never_asks() -> None:
    """This endpoint does not exist on a self-hosted Opik; asking costs a
    timeout on every install that can never be attributed anyway."""
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200))
    settings = _cloud_settings(opik_url="https://opik.acme-internal.example/api")
    assert resolve_api_key_identity(settings) is None
    time.sleep(0.1)  # give a stray thread the chance to prove us wrong
    assert not route.called


@respx.mock
def test_no_api_key_means_nothing_to_resolve() -> None:
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200))
    assert resolve_api_key_identity(_cloud_settings(opik_api_key=None)) is None
    time.sleep(0.1)
    assert not route.called


# --- startup is never blocked -------------------------------------------- #


@respx.mock
def test_a_cold_cache_returns_immediately_without_an_answer() -> None:
    """The first call must not wait for the network. It reports nothing, and the
    caller falls back to the install id with the discriminator saying so."""
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(200, json={"userName": "awkoy", "defaultWorkspaceName": "ws"})
    )
    started = time.monotonic()
    first = resolve_api_key_identity(_cloud_settings())
    elapsed = time.monotonic() - started
    assert first is None
    assert elapsed < 0.5, "resolution must not block the caller"


@respx.mock
def test_the_background_refresh_eventually_lands(_fresh_home: Path) -> None:
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"userName": "awkoy", "defaultWorkspaceName": "awkoy-v2"}
        )
    )
    identity = _await_resolution(_cloud_settings())
    assert identity is not None
    assert identity.user_name == "awkoy"
    assert identity.workspace_name == "awkoy-v2"
    # This endpoint has never returned a workspace UUID; claiming one would lie.
    assert identity.workspace_id is None


@respx.mock
def test_a_warm_cache_answers_with_no_network_call(_fresh_home: Path) -> None:
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200))
    _write_cache(_fresh_home)
    identity = resolve_api_key_identity(_cloud_settings())
    assert identity is not None
    assert identity.user_name == "cached-user"
    time.sleep(0.1)
    assert not route.called


@respx.mock
def test_a_stale_entry_is_served_while_it_refreshes(_fresh_home: Path) -> None:
    """A stale answer beats no answer; the correction arrives on a later event."""
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"userName": "renamed-user", "defaultWorkspaceName": "cached-ws"}
        )
    )
    _write_cache(_fresh_home, age_seconds=CACHE_TTL_SECONDS + 60)

    served = resolve_api_key_identity(_cloud_settings())
    assert served is not None
    assert served.user_name == "cached-user"

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        current = resolve_api_key_identity(_cloud_settings())
        if current is not None and current.user_name == "renamed-user":
            break
        time.sleep(0.02)
    else:
        pytest.fail("stale entry was never refreshed")


# --- credentials --------------------------------------------------------- #


def test_the_raw_key_is_never_written_to_disk(_fresh_home: Path) -> None:
    _write_cache(_fresh_home)
    raw = _cache_file(_fresh_home).read_text()
    assert API_KEY not in raw
    assert credential_digest(API_KEY) in raw


@respx.mock
def test_rotating_the_key_does_not_report_the_previous_user(_fresh_home: Path) -> None:
    """A cache keyed by the credential is what makes this safe."""
    _write_cache(_fresh_home)  # belongs to the OLD key
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"userName": "new-user", "defaultWorkspaceName": "new-ws"}
        )
    )
    rotated = _cloud_settings(opik_api_key="sk-rotated-key")

    immediate = resolve_api_key_identity(rotated)
    assert immediate is None, "the previous user must not be served for a new key"

    identity = _await_resolution(rotated)
    assert identity is not None
    assert identity.user_name == "new-user"


# --- every failure degrades quietly -------------------------------------- #


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["unexpected", "shape"]),
        httpx.Response(200, json={}),
    ],
    ids=["unauthorised", "server-error", "not-json", "wrong-shape", "identifies-nobody"],
)
def test_a_useless_response_leaves_us_anonymous(response: httpx.Response) -> None:
    respx.get(ACCOUNT_URL).mock(return_value=response)
    assert resolve_api_key_identity(_cloud_settings()) is None
    time.sleep(0.2)
    assert resolve_api_key_identity(_cloud_settings()) is None


@respx.mock
def test_an_unreachable_host_leaves_us_anonymous() -> None:
    respx.get(ACCOUNT_URL).mock(side_effect=httpx.ConnectError("nope"))
    assert resolve_api_key_identity(_cloud_settings()) is None
    time.sleep(0.2)
    assert resolve_api_key_identity(_cloud_settings()) is None


@respx.mock
def test_a_corrupt_cache_is_treated_as_empty(_fresh_home: Path) -> None:
    path = _cache_file(_fresh_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json")
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"userName": "awkoy", "defaultWorkspaceName": "awkoy-v2"}
        )
    )
    identity = _await_resolution(_cloud_settings())
    assert identity is not None
    assert identity.user_name == "awkoy"


@respx.mock
def test_an_unwritable_cache_still_resolves_for_this_process(_fresh_home: Path) -> None:
    """Losing the cache costs a lookup next boot, not the feature."""
    cache_dir = _fresh_home / ".opik-mcp"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # A directory where the cache file belongs: every write attempt fails.
    (cache_dir / "identity-cache.json").mkdir()
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"userName": "awkoy", "defaultWorkspaceName": "awkoy-v2"}
        )
    )
    identity = _await_resolution(_cloud_settings())
    assert identity is not None
    assert identity.user_name == "awkoy"


# --- a failing cache must not become a retry storm ----------------------- #


@respx.mock
def test_an_unwritable_cache_does_not_refetch_on_every_event(_fresh_home: Path) -> None:
    """The failure mode this module promises to avoid.

    With the cache unwritable, nothing persists between calls. Without an
    attempt floor, every single analytics event would start a fresh lookup —
    a request per event, forever, from a path whose failures are swallowed.
    """
    cache_dir = _fresh_home / ".opik-mcp"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "identity-cache.json").mkdir()  # every write attempt fails
    route = respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"userName": "awkoy", "defaultWorkspaceName": "awkoy-v2"}
        )
    )
    settings = _cloud_settings()
    assert _await_resolution(settings) is not None
    calls_after_first = route.call_count

    for _ in range(25):
        resolve_api_key_identity(settings)
    time.sleep(0.2)

    assert route.call_count == calls_after_first, (
        f"expected no further lookups, saw {route.call_count - calls_after_first}"
    )


@respx.mock
def test_a_rejected_key_is_not_retried_on_every_event(_fresh_home: Path) -> None:
    """An invalid key never resolves and never caches; it must still go quiet."""
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(401))
    settings = _cloud_settings()

    for _ in range(25):
        resolve_api_key_identity(settings)
    time.sleep(0.3)

    assert route.call_count <= 1, f"a rejected key was retried {route.call_count} times"


def test_the_disk_cache_is_read_once_not_once_per_event(
    _fresh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_build_event`` runs on the emitting thread — disk I/O per event would
    put a JSON parse on the caller's path."""
    import opik_mcp.account_identity as mod

    _write_cache(_fresh_home)
    reads: list[int] = []
    real = mod._read_cache

    def counting_read() -> Any:
        reads.append(1)
        return real()

    monkeypatch.setattr(mod, "_read_cache", counting_read)
    settings = _cloud_settings()
    for _ in range(10):
        assert resolve_api_key_identity(settings) is not None
    assert len(reads) == 1, f"disk was read {len(reads)} times"

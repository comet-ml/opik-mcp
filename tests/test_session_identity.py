"""Unit tests for the resolved-identity store.

The store is what lets a tool call report *who* is calling. It is keyed by a
digest of the credential rather than by the MCP session object, because the
identity is bound to the credential: one OAuth token resolves to exactly one
user and workspace for its whole lifetime, and the analytics layer that needs
the answer never sees the session object.

Two properties matter and are pinned here: the raw credential never becomes a
key, and the store cannot grow without bound on a long-lived hosted server that
sees a new token per user.
"""

from __future__ import annotations

import hashlib

from opik_mcp.session_identity import (
    MAX_TRACKED_CREDENTIALS,
    ResolvedIdentity,
    credential_digest,
    lookup_identity,
    remember_identity,
    reset_identities_for_tests,
)

RAW_TOKEN = "opik_mcp_at_CREDENTIAL-CANARY-MUST-NOT-BE-A-KEY-4f2a9b"


def _identity(user: str = "awkoy") -> ResolvedIdentity:
    return ResolvedIdentity(
        user_name=user,
        workspace_name="awkoy-v2",
        workspace_id="0190babc-62a0-71d2-832a-0feffa4676eb",
        source="oauth",
    )


def setup_function() -> None:
    reset_identities_for_tests()


def test_remembered_identity_is_readable_by_the_same_credential() -> None:
    remember_identity(RAW_TOKEN, _identity())
    found = lookup_identity(RAW_TOKEN)
    assert found is not None
    assert found.user_name == "awkoy"
    assert found.workspace_id == "0190babc-62a0-71d2-832a-0feffa4676eb"


def test_unknown_credential_resolves_to_nothing() -> None:
    assert lookup_identity("some-other-credential") is None


def test_raw_credential_never_appears_as_a_key() -> None:
    """The store lives for the process lifetime; a raw bearer must not sit in it."""
    remember_identity(RAW_TOKEN, _identity())
    assert RAW_TOKEN not in _keys()
    assert credential_digest(RAW_TOKEN) in _keys()


def test_digest_is_a_sha256_hex_of_the_credential() -> None:
    assert credential_digest(RAW_TOKEN) == hashlib.sha256(RAW_TOKEN.encode("utf-8")).hexdigest()


def test_rotating_the_credential_does_not_report_the_previous_user() -> None:
    remember_identity(RAW_TOKEN, _identity(user="old-user"))
    rotated = RAW_TOKEN + "-rotated"
    assert lookup_identity(rotated) is None
    remember_identity(rotated, _identity(user="new-user"))
    found = lookup_identity(rotated)
    assert found is not None
    assert found.user_name == "new-user"


def test_store_is_bounded_and_evicts_the_least_recently_used() -> None:
    """A hosted server sees one token per user; unbounded growth is a leak."""
    for i in range(MAX_TRACKED_CREDENTIALS + 10):
        remember_identity(f"token-{i}", _identity(user=f"user-{i}"))
    assert len(_keys()) <= MAX_TRACKED_CREDENTIALS
    # The oldest entries are gone, the newest survive.
    assert lookup_identity("token-0") is None
    assert lookup_identity(f"token-{MAX_TRACKED_CREDENTIALS + 9}") is not None


def test_reading_an_entry_keeps_it_from_being_evicted_first() -> None:
    for i in range(MAX_TRACKED_CREDENTIALS):
        remember_identity(f"token-{i}", _identity(user=f"user-{i}"))
    # Touch the oldest so it is no longer the least-recently-used.
    assert lookup_identity("token-0") is not None
    remember_identity("token-overflow", _identity(user="overflow"))
    assert lookup_identity("token-0") is not None
    assert lookup_identity("token-1") is None


def _keys() -> set[str]:
    from opik_mcp.session_identity import _STORE

    return set(_STORE)

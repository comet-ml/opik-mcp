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

from opik_mcp.credential_identity import (
    MAX_TRACKED_CREDENTIALS,
    ResolvedIdentity,
    credential_digest,
    lookup_identity,
    lookup_session_digest,
    remember_identity,
    remember_session,
    reset_identities_for_tests,
)

RAW_TOKEN = "opik_mcp_at_CREDENTIAL-CANARY-MUST-NOT-BE-A-KEY-4f2a9b"


def _identity(user: str = "awkoy") -> ResolvedIdentity:
    return ResolvedIdentity(
        user_name=user,
        workspace_name="awkoy-v2",
        workspace_id="0190babc-62a0-71d2-832a-0feffa4676eb",
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
    from opik_mcp.credential_identity import _STORE

    return set(_STORE)


# --- the credential -> session pairing ------------------------------------- #
#
# Needed because the MCP session id is minted in the RESPONSE to `initialize`,
# while every event is built in the session task forked from that request —
# whose context is frozen before the id exists. The credential is the only key
# present on both sides.

RAW_SESSION = "SESSION-CANARY-MUST-NOT-BE-STORED-RAW-7c3e11"


def test_the_raw_session_id_is_never_stored() -> None:
    """A session id is bearer-equivalent, so it gets credential treatment.

    Checks the stored VALUE, not just the key: this map holds session ids, and
    holding one in plaintext for the life of a pod is the thing to avoid.
    """
    reset_identities_for_tests()
    remember_session(RAW_TOKEN, RAW_SESSION)

    stored = lookup_session_digest(RAW_TOKEN)
    assert stored == hashlib.sha256(RAW_SESSION.encode("utf-8")).hexdigest()
    assert stored is not None and RAW_SESSION not in stored

    from opik_mcp.credential_identity import _SESSIONS

    blob = repr(list(_SESSIONS.items()))
    assert RAW_SESSION not in blob
    assert RAW_TOKEN not in blob


def test_an_unknown_credential_has_no_session() -> None:
    reset_identities_for_tests()
    remember_session(RAW_TOKEN, RAW_SESSION)
    assert lookup_session_digest("opik_mcp_at_never-seen") is None


def test_empty_inputs_are_ignored_rather_than_stored() -> None:
    """Callers must not have to guard — stdio passes no session at all."""
    reset_identities_for_tests()
    remember_session("", RAW_SESSION)
    remember_session(RAW_TOKEN, "")
    assert lookup_session_digest(RAW_TOKEN) is None
    assert lookup_session_digest("") is None


def test_the_session_map_cannot_grow_without_bound() -> None:
    """A hosted pod sees a new credential per user; this map must not leak."""
    reset_identities_for_tests()
    for i in range(MAX_TRACKED_CREDENTIALS + 50):
        remember_session(f"opik_mcp_at_token-{i}", f"session-{i}")

    from opik_mcp.credential_identity import _SESSIONS

    assert len(_SESSIONS) == MAX_TRACKED_CREDENTIALS
    # Least-recently-used went first; the newest survived.
    assert lookup_session_digest("opik_mcp_at_token-0") is None
    last = MAX_TRACKED_CREDENTIALS + 49
    assert lookup_session_digest(f"opik_mcp_at_token-{last}") is not None


def test_reading_a_session_keeps_it_from_being_evicted() -> None:
    """An in-use session must not lose its slot to one that has gone quiet."""
    reset_identities_for_tests()
    remember_session("opik_mcp_at_busy", "session-busy")
    for i in range(MAX_TRACKED_CREDENTIALS - 1):
        remember_session(f"opik_mcp_at_filler-{i}", f"session-{i}")

    # Touch the first one, then push the map over its bound.
    assert lookup_session_digest("opik_mcp_at_busy") is not None
    remember_session("opik_mcp_at_newcomer", "session-new")

    assert lookup_session_digest("opik_mcp_at_busy") is not None
    assert lookup_session_digest("opik_mcp_at_filler-0") is None


def test_a_re_handshake_on_the_same_credential_overwrites() -> None:
    """Documents the known imprecision rather than pretending it away.

    Re-initializing on the same credential repoints the pairing. Accepted: the
    alternative needs a session-unique key, and the task that builds events has
    none. A re-handshake means the client dropped the old session anyway.
    """
    reset_identities_for_tests()
    remember_session(RAW_TOKEN, "session-first")
    remember_session(RAW_TOKEN, "session-second")
    assert lookup_session_digest(RAW_TOKEN) == credential_digest("session-second")


def test_reset_clears_sessions_too() -> None:
    """Or one test's session leaks into the next."""
    reset_identities_for_tests()
    remember_session(RAW_TOKEN, RAW_SESSION)
    reset_identities_for_tests()
    assert lookup_session_digest(RAW_TOKEN) is None

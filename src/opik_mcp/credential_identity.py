"""Resolved caller identity, held explicitly and keyed by credential.

opik-mcp learns *who* is calling from the backend, not from local config: an
OAuth bearer is introspected on the ``initialize`` handshake, and an API key is
resolved against Comet's account-details endpoint. Both answers land here.

Keyed by a digest of the credential rather than by the MCP session object on
purpose:

- The identity is bound to the credential, not to the session. One OAuth token
  resolves to exactly one user and workspace for its whole lifetime, so a second
  session on the same token needs no second round-trip.
- ``analytics.client._build_event`` is where the answer is needed, and it never
  sees an MCP session object — it builds events synchronously in whatever task
  is emitting, including lifecycle emits that have no session at all.

The raw credential is never stored: it is hashed on the way in, both as key and
in the API surface, so a long-lived process never holds a bearer or an API key
in a process-level dict.

Bounded on purpose. A hosted deployment sees one token per user and would
otherwise grow this map for the life of the pod; eviction is least-recently-used
so an active session is never evicted in favour of a stale one.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

# One entry per active credential. Sized well above any realistic concurrent
# user count for a single pod while staying trivially small in memory.
MAX_TRACKED_CREDENTIALS = 512


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """What the backend told us about the holder of a credential.

    Every field is optional because every resolution path is best-effort: a
    failed lookup must degrade to anonymous telemetry, never to an error.

    There is deliberately no "which path resolved this" field: ``auth_mode``
    already tells BI whether the caller authenticated by OAuth or API key, and a
    second field saying the same thing could only ever disagree with it.
    """

    user_name: str | None
    workspace_name: str | None
    workspace_id: str | None


_STORE: OrderedDict[str, ResolvedIdentity] = OrderedDict()
# The store is read from the analytics dispatch thread and written from request
# handlers, so it needs its own lock — OrderedDict move_to_end plus popitem is
# not atomic under the GIL the way a single dict operation is.
_LOCK = threading.Lock()


def credential_digest(credential: str) -> str:
    """SHA-256 hex digest of a credential. The raw value never leaves the caller.

    The single implementation of this transform in the codebase. ``analytics``
    re-exports it under BI-facing names (``api_key_sha256``, ``token_sha256``)
    because those are contract terms in the event schema, but there is exactly
    one place the hashing actually happens — three hand-rolled copies of
    ``hashlib.sha256(x.encode("utf-8")).hexdigest()`` is three chances to drift
    on encoding or casing, and BI joins on the exact string.
    """
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def remember_identity(credential: str, identity: ResolvedIdentity) -> None:
    """Record what a credential resolved to, evicting the least-recently-used."""
    key = credential_digest(credential)
    with _LOCK:
        _STORE[key] = identity
        _STORE.move_to_end(key)
        while len(_STORE) > MAX_TRACKED_CREDENTIALS:
            _STORE.popitem(last=False)


def lookup_identity(credential: str) -> ResolvedIdentity | None:
    """What this credential resolved to, or ``None`` if we never found out."""
    key = credential_digest(credential)
    with _LOCK:
        identity = _STORE.get(key)
        if identity is not None:
            # Reading marks the credential as live, so an in-use session is not
            # evicted in favour of one that has gone quiet.
            _STORE.move_to_end(key)
        return identity


# Credential digest -> digest of the MCP session id minted on its handshake.
# Separate map rather than a field on ResolvedIdentity: a session is a different
# lifetime from an identity (a credential outlives any one session, and an
# api-key caller has a session but no resolved identity), so merging them would
# make one nullable for the other's sake.
_SESSIONS: OrderedDict[str, str] = OrderedDict()


def remember_session(credential: str, session_id: str) -> None:
    """Pair a credential with the MCP session id minted on its handshake.

    Exists because of an asymmetry in the streamable-HTTP lifecycle. The session
    id is minted in the RESPONSE to ``initialize``, but every event is built
    inside the MCP session task forked from that same request — and that task's
    context is frozen before the id exists. So the task can never observe the
    session id through a ContextVar, no matter which request carries the header:
    the requests that carry it build no events, and the context that builds
    events predates it. The credential is the only key present on both sides,
    which is what makes it the join.

    PRIVACY: the session id is stored HASHED. It is bearer-equivalent — holding
    it plus a token addresses a live session — so it gets the same treatment as
    a credential, and a long-lived process never holds one in plaintext.

    KNOWN IMPRECISION: re-initializing on the SAME credential overwrites the
    pairing, so if an older session is somehow still live it would report the
    newer digest. Accepted rather than papered over: the alternative needs a
    session-unique key, and no such key exists in the task that builds events.
    In practice a re-handshake means the client dropped the old session, and
    under OAuth the one-hour token TTL means a re-handshake usually arrives on a
    new credential anyway.

    No-ops on an empty credential or session id, so a caller never has to guard.
    """
    if not credential or not session_id:
        return
    key = credential_digest(credential)
    digest = credential_digest(session_id)
    with _LOCK:
        _SESSIONS[key] = digest
        _SESSIONS.move_to_end(key)
        while len(_SESSIONS) > MAX_TRACKED_CREDENTIALS:
            _SESSIONS.popitem(last=False)


def lookup_session_digest(credential: str) -> str | None:
    """Hashed MCP session id for this credential, or ``None`` if unknown.

    Already a digest — the caller emits it as-is and never re-hashes.
    """
    if not credential:
        return None
    key = credential_digest(credential)
    with _LOCK:
        digest = _SESSIONS.get(key)
        if digest is not None:
            # Reading marks it live, so an in-use session is not evicted in
            # favour of one that has gone quiet.
            _SESSIONS.move_to_end(key)
        return digest


def reset_identities_for_tests() -> None:
    """Drop every resolved identity and session pairing. Test-only."""
    with _LOCK:
        _STORE.clear()
        _SESSIONS.clear()


__all__ = [
    "MAX_TRACKED_CREDENTIALS",
    "ResolvedIdentity",
    "credential_digest",
    "lookup_identity",
    "lookup_session_digest",
    "remember_identity",
    "remember_session",
    "reset_identities_for_tests",
]

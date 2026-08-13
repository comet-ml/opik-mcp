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
    """SHA-256 hex digest of a credential. The raw value never leaves the caller."""
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


def reset_identities_for_tests() -> None:
    """Drop every resolved identity. Test-only — never call from production."""
    with _LOCK:
        _STORE.clear()


__all__ = [
    "MAX_TRACKED_CREDENTIALS",
    "ResolvedIdentity",
    "credential_digest",
    "lookup_identity",
    "remember_identity",
    "reset_identities_for_tests",
]

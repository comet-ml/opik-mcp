"""Resolve the caller's identity from a Comet API key.

The API-key install — the documented ``uvx`` path — knows nothing about who is
running it. The login lives on the Comet side, behind the same endpoint the Opik
Python SDK already uses to validate a key and find its default workspace:
``GET /api/rest/v2/account-details``, authenticated with the key itself. It
returns ``userName`` and ``defaultWorkspaceName``.

Three rules shape everything here, and all three exist to keep telemetry from
ever being felt by a user:

- **Startup never waits.** Boot reads a disk cache and moves on. A miss or a
  stale entry triggers a refresh on a background daemon thread; the events
  emitted before it lands are reported anonymously and say so.
- **Only where it can work.** Cloud deployments only. This endpoint does not
  exist on a self-hosted Opik, and a self-hosted install must not pay a timeout
  for an answer it can never get.
- **Failure is silent.** Unreachable host, non-200, malformed body, unreadable
  or unwritable cache — every one of them degrades to "we don't know", never to
  an error and never to a retry storm.

The cache is keyed by a digest of the key, never the key itself, so rotating a
credential resolves fresh identity instead of reporting the previous user, and
no new plaintext copy of a secret is written to disk.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from opik_mcp.config import Settings, installation_type
from opik_mcp.session_identity import (
    ResolvedIdentity,
    credential_digest,
    lookup_identity,
    remember_identity,
)

logger = logging.getLogger("opik_mcp.account_identity")

# Path of the Comet account-details endpoint, matching the Opik Python SDK
# (``opik.url_helpers.URL_ACCOUNT_DETAILS_POSTFIX``).
_ACCOUNT_DETAILS_PATH = "/api/rest/v2/account-details"

# A resolved login is stable for as long as the user does not rename themselves,
# so a day between refreshes is generous. Short enough that a rename corrects
# itself without anyone intervening.
CACHE_TTL_SECONDS = 24 * 60 * 60

# Deliberately tight: this never blocks anything a user waits on, but a hung
# connection should not keep a daemon thread alive for minutes either.
_TIMEOUT_SECONDS = 5.0

# One in-flight refresh per credential digest, so a burst of events at startup
# cannot fan out into a burst of identical HTTP calls.
_INFLIGHT: set[str] = set()
_INFLIGHT_LOCK = threading.Lock()


def _cache_path() -> Path:
    return Path.home() / ".opik-mcp" / "identity-cache.json"


def _account_details_url(settings: Settings) -> str | None:
    """The account-details URL for this deployment's Comet host, or ``None``.

    Built from the host only — the Opik REST base carries an ``/opik/api``
    suffix that this endpoint does not live under.
    """
    raw = settings.opik_url or settings.comet_url_override or ""
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{_ACCOUNT_DETAILS_PATH}"


def _read_cache() -> dict[str, Any]:
    try:
        return json.loads(_cache_path().read_text())  # type: ignore[no-any-return]
    except Exception:
        # Missing, unreadable or corrupt — all mean "no cached answer".
        return {}


def _write_cache(digest: str, user_name: str | None, workspace_name: str | None) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cache = _read_cache()
        cache[digest] = {
            "user_name": user_name,
            "workspace_name": workspace_name,
            "cached_at": time.time(),
        }
        path.write_text(json.dumps(cache))
        try:
            path.chmod(0o600)
        except OSError:
            logger.debug("could not chmod identity cache", exc_info=True)
    except Exception:
        # A read-only or full filesystem costs us the cache, not the feature:
        # the in-memory store still holds the answer for this process.
        logger.debug("identity cache not written", exc_info=True)


def _entry_is_fresh(entry: dict[str, Any]) -> bool:
    cached_at = entry.get("cached_at")
    if not isinstance(cached_at, int | float):
        return False
    return (time.time() - cached_at) < CACHE_TTL_SECONDS


def _identity_from_entry(entry: dict[str, Any]) -> ResolvedIdentity:
    return ResolvedIdentity(
        user_name=entry.get("user_name") or None,
        workspace_name=entry.get("workspace_name") or None,
        # This endpoint has never returned a workspace UUID. The OAuth path
        # supplies one; here the workspace name is the join key.
        workspace_id=None,
        source="api_key",
    )


def _fetch(url: str, api_key: str) -> tuple[str | None, str | None] | None:
    """``(user_name, default_workspace_name)`` from Comet, or ``None`` on any failure."""
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers={"Authorization": api_key})
        if response.status_code != 200:
            logger.debug("account-details returned %s", response.status_code)
            return None
        body = response.json()
    except Exception:
        logger.debug("account-details lookup failed", exc_info=True)
        return None
    if not isinstance(body, dict):
        return None
    user_name = body.get("userName")
    workspace_name = body.get("defaultWorkspaceName")
    return (
        user_name if isinstance(user_name, str) and user_name else None,
        workspace_name if isinstance(workspace_name, str) and workspace_name else None,
    )


def _refresh(url: str, api_key: str, digest: str) -> None:
    """Fetch and store. Runs on a daemon thread; must never raise."""
    try:
        fetched = _fetch(url, api_key)
        if fetched is None:
            return
        user_name, workspace_name = fetched
        if user_name is None and workspace_name is None:
            return
        remember_identity(
            api_key,
            ResolvedIdentity(
                user_name=user_name,
                workspace_name=workspace_name,
                workspace_id=None,
                source="api_key",
            ),
        )
        _write_cache(digest, user_name, workspace_name)
    except Exception:
        logger.debug("identity refresh failed", exc_info=True)
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(digest)


def resolve_api_key_identity(settings: Settings) -> ResolvedIdentity | None:
    """Identity for this install's API key, if we have one; refresh if we don't.

    Returns immediately, always. The answer comes from memory, then from the
    disk cache; a miss or a stale entry starts a background refresh whose result
    lands on a later event.
    """
    api_key = settings.opik_api_key
    if not api_key:
        return None
    # Self-hosted and local deployments have no account-details endpoint. Skip
    # entirely rather than spending a timeout to learn that every time.
    if installation_type(settings) != "cloud":
        return None

    known = lookup_identity(api_key)
    digest = credential_digest(api_key)
    entry = _read_cache().get(digest)

    if known is None and isinstance(entry, dict):
        known = _identity_from_entry(entry)
        remember_identity(api_key, known)

    if isinstance(entry, dict) and _entry_is_fresh(entry):
        return known

    url = _account_details_url(settings)
    if url is not None:
        with _INFLIGHT_LOCK:
            already_running = digest in _INFLIGHT
            if not already_running:
                _INFLIGHT.add(digest)
        if not already_running:
            threading.Thread(
                target=_refresh,
                args=(url, api_key, digest),
                name="opik-mcp-identity",
                daemon=True,
            ).start()

    # Whatever we have right now — possibly nothing, possibly a stale answer
    # that is still far better than none.
    return known


def reset_account_identity_for_tests() -> None:
    """Drop in-flight bookkeeping. Test-only — never call from production."""
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()


__all__ = [
    "CACHE_TTL_SECONDS",
    "reset_account_identity_for_tests",
    "resolve_api_key_identity",
]

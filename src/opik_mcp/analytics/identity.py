"""Stable identity resolvers for analytics events.

- ``get_install_id()``: per-laptop UUID4 persisted at ``~/.opik-mcp/install-id``.
  Mirrors ``MetadataDAO.ANONYMOUS_ID`` in opik-backend, file-backed.
- ``resolve_anonymous_id(settings)``: top-level ``user_id`` for comet-stats —
  workspace name → install_id. **Kept stable on purpose**; the per-user
  identity ships as ``event_properties.api_key_sha256`` so BI dashboards
  built against the old ``user_id`` semantics keep working.
- ``api_key_sha256(key)``: per-user pseudonymous identity. SHA-256 of the
  OPIK_API_KEY. The backend retains the raw-key → user-id mapping; BI joins
  on the digest. The raw key NEVER leaves this module.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from opik_mcp.config import Settings

logger = logging.getLogger("opik_mcp.analytics.identity")

# Stable fallback returned when the filesystem is unavailable (HOME unset, read-only).
# Using the nil UUID makes it visually obvious in analytics dashboards that the
# device identity is unknown rather than silently wrong.
_FALLBACK_INSTALL_ID = "00000000-0000-0000-0000-000000000000"


def _install_id_path() -> Path:
    return Path.home() / ".opik-mcp" / "install-id"


@lru_cache(maxsize=1)
def _get_install_id() -> tuple[str, bool]:
    """Returns ``(install_id, was_freshly_generated_this_process)``.

    The boolean flag enables BI to distinguish brand-new installs (flag True
    on the first process after install) from returning users (flag False).
    Process-stable thanks to ``lru_cache``: every emit during this process
    sees the same answer.
    """
    try:
        path = _install_id_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                raw = path.read_text().strip()
                return (str(UUID(raw)), False)
            except (ValueError, OSError):
                logger.warning("install-id file unreadable or malformed; regenerating")
        new_id = str(uuid4())
        path.write_text(new_id)
        try:
            path.chmod(0o600)
        except OSError:
            logger.debug("could not chmod install-id file", exc_info=True)
        return (new_id, True)
    except Exception:
        # Fallback is NOT "freshly generated" — it's an unwritable-fs sentinel
        # and treating it as "new" would inflate the install-funnel.
        logger.warning(
            "install-id unavailable (HOME unset or read-only filesystem); using fallback id=%s",
            _FALLBACK_INSTALL_ID,
            exc_info=True,
        )
        return (_FALLBACK_INSTALL_ID, False)


def get_install_id() -> str:
    return _get_install_id()[0]


def install_id_was_freshly_generated() -> bool:
    """True iff this process is the one that just wrote the install-id file."""
    return _get_install_id()[1]


def api_key_sha256(api_key: str) -> str:
    """SHA-256 hex digest of the API key. Stable, irreversible, per-user.

    The backend retains the raw-key → user-id mapping; BI can JOIN on the
    digest to recover the Comet user account without ever seeing plaintext.
    Lowercase hex (64 chars) matches the convention used elsewhere in Comet
    (e.g. ``hashlib.sha256(...).hexdigest()`` defaults).
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def resolve_anonymous_id(settings: Settings) -> str:
    """Top-level ``user_id`` for comet-stats: workspace name → install_id.

    Intentionally does NOT include the api_key hash. comet-stats indexes
    events by ``user_id`` and existing Metabase / Looker dashboards filter
    and join on workspace strings; flipping that field to a 64-char hex
    digest would discontinuously break those queries. The per-user identity
    is exposed as ``event_properties.api_key_sha256`` instead — BI can
    migrate join keys on its own schedule.
    """
    return settings.comet_workspace or get_install_id()

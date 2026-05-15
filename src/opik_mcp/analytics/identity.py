"""Stable anonymous_id resolver — workspace if set, else persisted install UUID.

Mirrors `MetadataDAO.ANONYMOUS_ID` in opik-backend, file-backed instead of DB-backed.
"""

from __future__ import annotations

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
def _get_install_id() -> str:
    try:
        path = _install_id_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                raw = path.read_text().strip()
                return str(UUID(raw))
            except (ValueError, OSError):
                logger.warning("install-id file unreadable or malformed; regenerating")
        new_id = str(uuid4())
        path.write_text(new_id)
        try:
            path.chmod(0o600)
        except OSError:
            # On Windows / odd filesystems chmod may not apply — best-effort, not fatal.
            logger.debug("could not chmod install-id file", exc_info=True)
        return new_id
    except Exception:
        # HOME unset or filesystem is read-only — log once (lru_cache ensures this
        # body runs only once per process) and return a stable sentinel value so the
        # caller never sees an exception and analytics still has a consistent id.
        logger.warning(
            "install-id unavailable (HOME unset or read-only filesystem); using fallback id=%s",
            _FALLBACK_INSTALL_ID,
            exc_info=True,
        )
        return _FALLBACK_INSTALL_ID


def get_install_id() -> str:
    return _get_install_id()


def resolve_anonymous_id(settings: Settings) -> str:
    return settings.comet_workspace or get_install_id()

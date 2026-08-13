"""Stable identity resolvers for analytics events.

- ``get_install_id()``: per-laptop UUID4 persisted at ``~/.opik-mcp/install-id``.
  Mirrors ``MetadataDAO.ANONYMOUS_ID`` in opik-backend, file-backed.
- ``api_key_sha256(key)``: SHA-256 of the OPIK_API_KEY, emitted as a stable
  pseudonymous per-credential label. NOTE: it is not a usable join key on its
  own — the warehouse holds no api-key-hash → user mapping, which is why real
  identity is resolved from the backend instead (see ``credential_identity``).
  The raw key NEVER leaves this module.

The top-level ``user_id`` is assembled in ``analytics.client``; see the note at
the foot of this module for what used to live here and why it is gone.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import UUID, uuid4

from opik_mcp.credential_identity import credential_digest

logger = logging.getLogger("opik_mcp.analytics.identity")


def _resolve_opik_mcp_version() -> str:
    # Prefer the build-generated _version.py (carries the exact CI/release version,
    # e.g. the hosted image's 0.2.N). Fall back to installed package metadata, then
    # to "unknown" for an uninstalled/un-generated tree.
    try:
        from opik_mcp._version import __version__

        return __version__
    except ImportError:
        pass
    try:
        return version("opik-mcp")
    except PackageNotFoundError:
        return "unknown"


# Cached once at import time — package metadata is static for the process
# lifetime. Single source of truth for both BI (``library_version``) and
# Sentry (``release`` tag); without this the two channels could drift if
# one consumer changed the lookup mechanism.
OPIK_MCP_VERSION: str = _resolve_opik_mcp_version()

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
    """SHA-256 hex digest of the API key. Stable, irreversible, per-credential.

    A useful label for grouping calls made with the same key. It is NOT a user
    join key: this module used to claim the backend retained a raw-key → user-id
    mapping BI could join on, and it does not — a digest join against the
    warehouse returns zero matches. Real identity is resolved from the backend
    (see ``credential_identity``); this stays as a credential-level label only.

    Lowercase hex (64 chars) matches the convention used elsewhere in Comet.
    Delegates to ``credential_digest`` so every digest in the codebase is the
    same transform; this function exists for the BI-facing name.
    """
    return credential_digest(api_key)


# NOTE: there is deliberately no `resolve_anonymous_id` here any more.
#
# It used to compute the top-level `user_id` as "workspace name → install_id",
# kept that way so dashboards built on the old meaning would not break. The
# warehouse showed what that cost: 75.1% of events carried a workspace name in
# the user field, 24.8% an install id, and 0% an actual user — a column that
# looked fully populated while answering the wrong question.
#
# `user_id` is now the caller's Comet login (see `client._build_event`), falling
# back to `get_install_id()` only when no identity could be resolved, with
# `user_id_kind` saying which. Do not reintroduce the workspace fallback.

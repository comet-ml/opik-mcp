"""Which identity applies to the call being made right now.

Three modules can each answer "who is this": ``oauth_identity`` introspects an
inbound bearer, ``account_identity`` resolves this install's own API key, and
``credential_identity`` remembers what either of them found. This module owns the
one policy question that sits above all three — *whose* credential is in play —
so that the analytics layer can stamp an answer without also deciding it.

The rule is short and worth stating plainly, because getting it wrong
misattributes usage rather than merely losing it:

- An **inbound bearer** belongs to the caller, not to this process. Its identity
  is whatever the handshake resolved and stored against that token. If nothing
  was stored, the call is anonymous — falling back to this server's own
  configured identity would credit somebody else's work to the operator.
- **No inbound credential** means stdio, or unauthenticated HTTP. Here the
  install's own API key *is* the caller, so resolving it against settings is
  correct.
"""

from __future__ import annotations

import logging

from opik_mcp.account_identity import resolve_api_key_identity
from opik_mcp.auth_context import classify_bearer, inbound_authorization
from opik_mcp.config import Settings
from opik_mcp.credential_identity import ResolvedIdentity, lookup_identity

logger = logging.getLogger("opik_mcp.caller_identity")


def caller_identity(settings: Settings) -> ResolvedIdentity | None:
    """The identity of whoever is making this call, or ``None`` if unknown.

    Never raises and never blocks: identity is a telemetry nicety, and the call
    it describes must not be affected by it in any way.
    """
    try:
        inbound_auth = inbound_authorization.get()
        if inbound_auth:
            _mode, token = classify_bearer(inbound_auth)
            # Only an OAuth bearer has an identity we could have resolved; a
            # forwarded API-key-shaped credential is opaque to us.
            return lookup_identity(token) if token else None
        return resolve_api_key_identity(settings)
    except Exception:
        logger.debug("caller identity resolution failed", exc_info=True)
        return None


__all__ = ["caller_identity"]

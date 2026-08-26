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
from opik_mcp.config import Settings, installation_type
from opik_mcp.credential_identity import ResolvedIdentity, lookup_identity

logger = logging.getLogger("opik_mcp.caller_identity")


def caller_identity(settings: Settings) -> ResolvedIdentity | None:
    """The identity of whoever is making this call, or ``None`` if unknown.

    Never raises and never blocks: identity is a telemetry nicety, and the call
    it describes must not be affected by it in any way.
    """
    return caller_identity_with_outcome(settings)[0]


def caller_identity_with_outcome(settings: Settings) -> tuple[ResolvedIdentity | None, str]:
    """The caller's identity AND why it came out that way.

    The second element feeds the ``identity_lookup`` BI field, and it exists
    because ``None`` has two meanings that must never be summed:

    - Nobody presented a credential, so anonymity is correct. Local and
      self-hosted Opik run with auth disabled by design.
    - A credential WAS presented and we still could not resolve it. That is a
      defect, and it is the number that says whether hosted identity works.

    Both previously emitted ``user_id_kind='install_id'`` and were therefore
    indistinguishable in the warehouse — which is exactly why a hosted deploy
    could not be verified from its own telemetry.

    One honest imprecision on the settings-API-key path: resolution is
    asynchronous, so the first events of a fresh cloud process can report "miss"
    while the background refresh is still in flight. It self-corrects within the
    session. Read "miss" per transport rather than fleet-wide.
    """
    try:
        inbound_auth = inbound_authorization.get()
        if inbound_auth:
            _mode, token = classify_bearer(inbound_auth)
            if not token:
                # An API-key-shaped credential forwarded to this server. A
                # credential was presented and we cannot resolve it at all —
                # there is no inbound-API-key resolution path today. A miss, not
                # by-design anonymity, and on a hosted server it is the reason
                # api-key callers can never be counted as people.
                return (None, "miss")
            identity = lookup_identity(token)
            if identity is not None and identity.user_name:
                return (identity, "resolved")
            # The handshake should have stored this. It did not: introspection
            # failed, the pod restarted and emptied the store, or the LRU evicted
            # a live credential.
            return (None, "miss")

        identity = resolve_api_key_identity(settings)
        if identity is not None and identity.user_name:
            return (identity, "resolved")
        if not settings.opik_api_key or installation_type(settings) != "cloud":
            # No credential, or a deployment with no account-details endpoint to
            # ask. Anonymous by construction, not by failure.
            return (None, "none_expected")
        return (None, "miss")
    except Exception:
        logger.debug("caller identity resolution failed", exc_info=True)
        return (None, "miss")


__all__ = ["caller_identity", "caller_identity_with_outcome"]

"""What a failed Opik call is, and what the caller can do about it.

Each class here is what a backend status becomes (``raise_for_status``);
``hints`` says what to change for the failures a setup change fixes.
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from opik_mcp.client.errors.hints import credential_hint
from opik_mcp.config import DOCS_WORKSPACE
from opik_mcp.error_kinds import ErrorKind
from opik_mcp.identity.context import (
    classify_bearer,
    inbound_authorization,
    oauth_token_expired_hint,
)
from opik_mcp.identity.store import forget_validation

# Each class carries its own ``error_kind`` + ``http_status`` as a
# ``ClassVar`` so ``analytics/errors.py`` can route via ``getattr`` instead
# of an ``isinstance`` cascade. ``OpikPermissionError`` shadows its parent's
# values — Python's attribute resolution picks the subclass automatically,
# so the analytics layer needs no special "permission before auth" ordering.


class OpikAuthError(RuntimeError):
    """Opik rejected the credential (401): a bad API key, or an OAuth access token
    that expired or was revoked (see ``identity.context.oauth_token_expired_hint``)."""

    error_kind: ClassVar[ErrorKind] = "auth"
    http_status: ClassVar[int | None] = 401


class OpikPermissionError(OpikAuthError):
    """Opik returned 403 — caller is authenticated but not allowed for the
    target workspace / resource. Subclass of ``OpikAuthError`` so existing
    handlers that catch the auth case continue to catch this too; the
    ``error_kind`` / ``http_status`` ClassVars shadow the parent's so
    analytics still distinguish the two.
    """

    error_kind: ClassVar[ErrorKind] = "permission"
    http_status: ClassVar[int | None] = 403


class OpikNotFoundError(RuntimeError):
    """Target entity does not exist (404). Wraps the entity hint."""

    error_kind: ClassVar[ErrorKind] = "not_found"
    http_status: ClassVar[int | None] = 404


class OpikValidationError(RuntimeError):
    """Opik rejected the request body (400/422).

    ``backend_reason`` is the backend's own capped reason, for a caller that
    reports it in a field of its own rather than inside this message.
    """

    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400

    def __init__(self, message: str, *, backend_reason: str | None = None) -> None:
        super().__init__(message)
        self.backend_reason = backend_reason


class OpikServerError(RuntimeError):
    """Opik returned a 5xx response."""

    error_kind: ClassVar[ErrorKind] = "upstream_5xx"
    http_status: ClassVar[int | None] = 500


def note_backend_401() -> str | None:
    """opik-backend just answered 401 to the call this request is forwarding.

    If the inbound bearer is an OAuth token, its cached validation is dropped so
    the NEXT MCP request re-asks the backend and gets the ``invalid_token`` 401
    that triggers the host's refresh — now, not after the cache TTL. Returns the
    tool-error hint for that bearer (``None`` for an API key); see
    ``identity.context.oauth_token_expired_hint``. Called from every place a backend
    401 is turned into an error: here for reads/lists, ``writes.dispatch`` for
    writes, and the Diagnostics follow-up PATCH in
    ``writes.operations.diagnostics``.
    """
    auth = inbound_authorization.get()
    if auth:
        mode, token = classify_bearer(auth)
        if mode == "oauth":
            forget_validation(token)
    return oauth_token_expired_hint()


#: Room for a validation reason or two; a longer one is cut and ends in "…".
_BACKEND_REASON_CHARS = 200


def backend_reason(resp: httpx.Response) -> str | None:
    """The backend's own words on a 400, 409 or 422, capped; ``None`` otherwise.

    Only the strings under ``errors`` (Opik's ``ErrorMessage``) or ``message``
    (Dropwizard's), joined and on one line — never the rest of the body, and
    never for a status where the text is about the server rather than the
    request. A 409 is included because it says which conflict: a trace update
    under the wrong project and a create of an existing id both answer 409.
    """
    if resp.status_code not in (400, 409, 422):
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    errors, message = body.get("errors"), body.get("message")
    strings = [e for e in errors if isinstance(e, str)] if isinstance(errors, list) else []
    if not strings and isinstance(message, str):
        strings = [message]
    text = " ".join("; ".join(strings).split()).replace('"', "'")
    if len(text) > _BACKEND_REASON_CHARS:
        text = text[: _BACKEND_REASON_CHARS - 1] + "…"
    return text or None


def raise_for_status(resp: httpx.Response, entity_hint: str) -> None:
    """One sentence per status: what was asked and what to change.

    The backend's body and the REST path stay out: the body is untrusted text
    that can carry anything the request did, and the path is not a name the
    caller can use.
    """
    status = resp.status_code
    if 200 <= status < 300:
        return
    if status == 401:
        hint = note_backend_401() or credential_hint()
        raise OpikAuthError(f"Opik rejected the credential for {entity_hint} (401). {hint}")
    if status == 403:
        raise OpikPermissionError(
            f"Permission denied for {entity_hint} (403). Use a credential for the "
            f"workspace that owns it: {DOCS_WORKSPACE}"
        )
    if status == 404:
        raise OpikNotFoundError(
            f"{entity_hint} not found (404). Check the id or name and the workspace."
        )
    if status in (400, 422):
        reason = backend_reason(resp)
        said = f' Backend said: "{reason}"' if reason else ""
        raise OpikValidationError(
            f"Opik rejected the request for {entity_hint} ({status}). Check the "
            f"arguments passed.{said}",
            backend_reason=reason,
        )
    if status >= 500:
        raise OpikServerError(
            f"Opik server error ({status}) for {entity_hint}. Retry the same call."
        )
    # 3xx / unexpected 2xx are already handled by the caller.
    raise OpikServerError(f"Unexpected status {status} for {entity_hint}.")

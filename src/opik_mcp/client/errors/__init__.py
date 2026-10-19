"""What a failed Opik call is, and what the caller can do about it.

Each class here is what a backend status becomes (``raise_for_status``);
``hints`` says what to change for the failures a setup change fixes.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal, NoReturn

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


ServerFailure = Literal["fast", "slow", "gateway"]
"""How a 5xx failed, which decides whether repeating the call can help."""


class OpikServerError(RuntimeError):
    """Opik returned a 5xx response, or an answer this client cannot use.

    ``log_id`` is the id the backend logged the failure under, when its body
    carried one: what a self-hosted admin searches the backend log for.
    """

    error_kind: ClassVar[ErrorKind] = "upstream_5xx"
    http_status: ClassVar[int | None] = 500
    server_failure: ClassVar[ServerFailure | None] = None

    def __init__(self, message: str, *, log_id: str | None = None) -> None:
        super().__init__(message)
        self.log_id = log_id


class OpikRequestFailedError(OpikServerError):
    """Opik answered 5xx quickly: the request itself fails, so repeating it fails too."""

    server_failure: ClassVar[ServerFailure | None] = "fast"


class OpikQueryTimeoutError(OpikServerError):
    """Opik answered 5xx after a long wait: its query most likely timed out."""

    server_failure: ClassVar[ServerFailure | None] = "slow"


class OpikGatewayError(OpikServerError):
    """A gateway in front of Opik answered 502, 503 or 504, which is often transient."""

    server_failure: ClassVar[ServerFailure | None] = "gateway"


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


#: A 5xx that took this long was spent waiting on a query. Backends before
#: 2.2.90 turn a ClickHouse query over about 10 s into a 500; a request that
#: fails on its own (a getById that finds duplicate rows) answers in well under
#: a second.
_QUERY_TIMEOUT_AFTER_S = 5.0

GATEWAY_STATUSES = (502, 503, 504)

# Dropwizard's body for an unhandled exception: "There was an error processing
# your request. It has been logged (ID %016x)." Only the hex id is kept.
_BACKEND_LOG_ID = re.compile(r"It has been logged \(ID ([0-9a-f]{16})\)")


def backend_log_id(resp: httpx.Response) -> str | None:
    """The id a 5xx was logged under on the backend, from its generic body."""
    found = _BACKEND_LOG_ID.search(resp.text)
    return found.group(1) if found else None


def server_failure(resp: httpx.Response) -> ServerFailure:
    """Which kind of 5xx this is, from its status and how long it took."""
    if resp.status_code in GATEWAY_STATUSES:
        return "gateway"
    if resp.elapsed.total_seconds() >= _QUERY_TIMEOUT_AFTER_S:
        return "slow"
    return "fast"


def log_id_sentence(log_id: str | None) -> str:
    """A trailing sentence naming the backend log id, or nothing."""
    return f" The Opik backend log has it under ID {log_id}." if log_id else ""


def _raise_server_error(resp: httpx.Response, entity_hint: str) -> NoReturn:
    status = resp.status_code
    log_id = backend_log_id(resp)
    logged = log_id_sentence(log_id)
    failure = server_failure(resp)
    if failure == "gateway":
        raise OpikGatewayError(
            f"Opik's gateway answered {status} for {entity_hint}; this is often "
            f"transient, so retry once after a short wait.{logged}",
            log_id=log_id,
        )
    if failure == "slow":
        # How to narrow depends on the call's arguments, so the caller that
        # has them (the list tool) adds it.
        raise OpikQueryTimeoutError(
            f"Opik took {resp.elapsed.total_seconds():.0f} s and failed on {entity_hint} "
            f"({status}), most likely a query timeout.{logged}",
            log_id=log_id,
        )
    raise OpikRequestFailedError(
        f"Opik failed on {entity_hint} ({status}), and the same call usually fails "
        f"the same way; don't repeat it.{logged}",
        log_id=log_id,
    )


def raise_for_status(resp: httpx.Response, entity_hint: str) -> None:
    """One sentence per status: what was asked and what to change.

    The backend's body and the REST path stay out: the body is untrusted text
    that can carry anything the request did, and the path is not a name the
    caller can use. A 5xx keeps only the log id, hex matched by a regex.
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
        _raise_server_error(resp, entity_hint)
    # 3xx / unexpected 2xx are already handled by the caller.
    raise OpikServerError(f"Unexpected status {status} for {entity_hint}.")

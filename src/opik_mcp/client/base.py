"""The transport, errors and config every ``OpikClient`` endpoint group shares."""

from __future__ import annotations

import json as _json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import ClassVar, Final

import httpx

from opik_mcp.client.json_value import JsonObject
from opik_mcp.config import (
    DEFAULT_WORKSPACE,
    WORKSPACE_ENV_VARS,
    MissingConfigError,
    Settings,
    looks_unsubstituted,
    unfilled_workspace_error,
)
from opik_mcp.error_kinds import ErrorKind
from opik_mcp.identity.context import (
    OAUTH_ACCESS_TOKEN_PREFIX,
    classify_bearer,
    inbound_authorization,
    inbound_workspace,
    oauth_token_expired_hint,
)
from opik_mcp.identity.store import forget_validation

# --- errors --------------------------------------------------------------- #
#
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

    ``status`` is the one the backend answered with, and ``backend_reason`` its
    own capped reason, for a caller that reports them in fields of their own
    rather than inside this message. ``http_status`` stays 400: it is the
    class's analytics bucket, not the response.
    """

    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400

    def __init__(
        self, message: str, *, status: int = 400, backend_reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.backend_reason = backend_reason


class OpikServerError(RuntimeError):
    """Opik returned a 5xx response."""

    error_kind: ClassVar[ErrorKind] = "upstream_5xx"
    http_status: ClassVar[int | None] = 500


# --- client --------------------------------------------------------------- #

_DEFAULT_TIMEOUT: Final = 30.0


type QueryParams = dict[str, str | int]
"""Query parameters as httpx sends them: every value is a string or a number."""


def _drop_none[V](d: Mapping[str, V | None]) -> dict[str, V]:
    return {k: v for k, v in d.items() if v is not None}


def _ids_param(ids: list[str]) -> str:
    """A list of ids as opik-backend's ``experiment_ids`` query param.

    ``ParamsValidator.getIds`` deserializes the whole param as JSON into a
    ``List<UUID>``, so it is a JSON array and not the comma-separated list
    every other multi-value param in this API uses. Comma-joined reaches the
    caller as ``Invalid query param ids`` (400), which says nothing about the
    format it wanted — so it is written once, here.
    """
    return _json.dumps(list(ids), separators=(",", ":"))


def _search_params(
    *,
    filters: str | None,
    sorting: str | None,
    search: str | None,
    from_time: str | None,
    to_time: str | None,
    should_truncate: bool | None,
) -> dict[str, str]:
    """Query params shared by the searchable list endpoints (traces, spans,
    threads, experiments). Only set values are sent: the backend treats an
    empty ``filters=`` as malformed JSON and answers 400.

    ``should_truncate`` is rendered as the lowercase literal the backend's boolean
    query param parser expects (httpx would otherwise send ``True``)."""
    params = _drop_none(
        {
            "filters": filters,
            "sorting": sorting,
            "search": search,
            "from_time": from_time,
            "to_time": to_time,
        }
    )
    if should_truncate is not None:
        params["truncate"] = "true" if should_truncate else "false"
    return params


class OpikClientBase:
    """Transport every endpoint mixin shares: the bound credential, workspace and session.

    Workspace is constructor-bound — the MCP tool layer never passes it.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        workspace: str | None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._workspace = workspace
        self._client = client
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Optional — self-hosted backends run with auth disabled and authorize on the
        # workspace header alone, so only send Authorization when a key is configured.
        if self._api_key:
            headers["Authorization"] = self._api_key
        # Omitted for OAuth tokens — opik-backend derives the workspace from the token row
        if self._workspace:
            headers["Comet-Workspace"] = self._workspace
        return headers

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            yield c

    async def _get_json(
        self,
        path: str,
        *,
        params: QueryParams | None,
        entity_hint: str,
    ) -> JsonObject:
        """GET with the standard headers, expect 200, return parsed JSON.

        Non-2xx maps to the same typed errors as the write path via
        ``_raise_for_status`` so resource callers don't have to translate.
        """
        url = f"{self._base_url}{path}"
        async with self._http() as http:
            resp = await http.request("GET", url, params=params, headers=self._headers())
        _raise_for_status(resp, entity_hint)
        if resp.status_code != 200:
            raise OpikServerError(
                f"Unexpected status {resp.status_code} for {entity_hint} (expected 200)."
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise OpikServerError(f"Opik returned a non-JSON answer for {entity_hint}.") from exc
        if not isinstance(body, dict):
            raise OpikServerError(
                f"Opik returned non-object JSON for {entity_hint}: {type(body).__name__}."
            )
        return body

    async def _post_json(
        self,
        path: str,
        *,
        json: Mapping[str, object],
        entity_hint: str,
    ) -> JsonObject:
        """POST a JSON body, expect 200, return the parsed object.

        Read-side sibling of ``_get_json`` for endpoints the backend models as
        POST-with-body rather than ``GET /{id}`` (thread ``retrieve``). Same
        typed error mapping via ``_raise_for_status`` so callers don't translate.
        """
        url = f"{self._base_url}{path}"
        content = _json.dumps(json, separators=(",", ":")).encode()
        async with self._http() as http:
            resp = await http.request("POST", url, content=content, headers=self._headers())
        _raise_for_status(resp, entity_hint)
        if resp.status_code != 200:
            raise OpikServerError(
                f"Unexpected status {resp.status_code} for {entity_hint} (expected 200)."
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise OpikServerError(f"Opik returned a non-JSON answer for {entity_hint}.") from exc
        if not isinstance(body, dict):
            raise OpikServerError(
                f"Opik returned non-object JSON for {entity_hint}: {type(body).__name__}."
            )
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object],
        expected_status: int,
        entity_hint: str,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        # We serialize manually so dicts are emitted in insertion order and the
        # body is byte-stable for tests; httpx's default json= keeps insertion
        # order in 3.13 too, but encoding it ourselves removes that dependency.
        content = _json.dumps(json, separators=(",", ":")).encode()
        async with self._http() as http:
            resp = await http.request(method, url, content=content, headers=self._headers())
        _raise_for_status(resp, entity_hint)
        if resp.status_code != expected_status:
            # Body present but wrong code (e.g. 200 instead of 204) — not fatal
            # by itself, but it means the contract changed; surface it.
            raise OpikServerError(
                f"Unexpected status {resp.status_code} for {entity_hint} "
                f"(expected {expected_status})."
            )
        return resp

    async def write_json(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | Sequence[object],
        *,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        """Generic write — used by the universal write tool's dispatcher.

        Unlike ``_request``, this does NOT raise on 4xx/5xx; the dispatcher
        wraps non-2xx responses into structured ``BackendError`` envelopes
        so the model sees the BE's body verbatim alongside the request
        shape. 2xx with non-empty body is returned as-is for the caller to
        parse (some endpoints echo the created entity).
        """
        url = f"{self._base_url}{path}"
        # Stable byte-order serialization (matches ``_request``) so respx-based
        # tests can assert on the exact request body. The dispatcher's ``_dump``
        # already JSON-serializes datetimes/UUIDs via ``model_dump(mode='json')``,
        # so anything reaching here is JSON-primitive — we deliberately omit
        # ``default=`` so a stray non-JSON value surfaces as ``TypeError`` here
        # rather than getting silently stringified into a malformed wire shape
        # the BE would reject far away from the source.
        content = _json.dumps(body, separators=(",", ":")).encode()
        headers = self._headers()
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        async with self._http() as http:
            return await http.request(method, url, content=content, headers=headers)


def resolve_opik_config(settings: Settings) -> tuple[str, str | None, str | None]:
    """Resolve ``(opik_base_url, api_key, workspace)`` from settings or raise.

    Centralizes the rule for deriving Opik's REST base from either an explicit
    ``OPIK_URL`` override or ``COMET_URL_OVERRIDE + "/opik/api"``. Both the
    score/comment orchestrator and the resource layer call this so the config
    contract lives in exactly one place.

    **Per-request bearer + workspace forwarding.** When the process is
    serving an inbound HTTP request that carried an ``Authorization``
    header (OAuth-passthrough mode), the middleware populates
    :mod:`opik_mcp.identity.context` ContextVars and we prefer those over the
    env-bound ``OPIK_API_KEY`` / ``COMET_WORKSPACE``. opik-backend's
    ``AuthFilter`` accepts both shapes (API key and an
    ``OAUTH_ACCESS_TOKEN_PREFIX``-prefixed ``Bearer``) and enforces
    ``@RequiredPermissions`` per endpoint, so opik-mcp is a
    thin forwarder either way.
    """
    inbound_auth = inbound_authorization.get()
    inbound_ws = inbound_workspace.get()
    # Optional: self-hosted backends run with auth disabled and authorize on the
    # workspace header alone. When absent, requests go out without an Authorization
    # header and the backend decides (a hosted Opik rejects with 401, surfaced as a
    # normal request error). Mirrors the workspace handling just below.
    api_key = inbound_auth if inbound_auth else settings.opik_api_key
    # OAuth access tokens carry their workspace server-side (opik-backend
    # derives it from the token row). Identified by token prefix — mirrors
    # the backend's McpOAuthTokenUtils.isMcpOAuthToken — so an API key that
    # merely contains the marker can't skip the workspace requirement.
    oauth_passthrough = False
    if inbound_auth is not None:
        scheme, _, token = inbound_auth.partition(" ")
        oauth_passthrough = scheme.lower() == "bearer" and token.lstrip().startswith(
            OAUTH_ACCESS_TOKEN_PREFIX
        )
    if oauth_passthrough:
        # Workspace is derived from the token server-side; may be None here.
        workspace = inbound_ws
    else:
        # Workspace is optional: inbound Comet-Workspace header, else the
        # configured workspace, else "default" (Opik SDK convention). No hard
        # failure — lets local/OSS users run without setting a workspace.
        workspace = inbound_ws or settings.comet_workspace or DEFAULT_WORKSPACE
    if workspace and looks_unsubstituted(workspace):
        # Fail here with something actionable rather than forwarding a
        # placeholder and letting the backend answer with an auth error that
        # names neither the setting nor the value. Classified as `validation`
        # (see MissingConfigError) so it buckets as a fixable setup problem.
        source = "the inbound Comet-Workspace header" if inbound_ws else WORKSPACE_ENV_VARS
        raise unfilled_workspace_error(workspace, source)
    base = opik_rest_base(settings)
    if base is None:
        # ``comet_url_override`` has a non-empty default in ``Settings`` but
        # ``COMET_URL_OVERRIDE=""`` would override it to empty — defend against
        # that so we never POST to ``/opik/api`` (relative URL → wherever the
        # process happens to be).
        raise MissingConfigError("OPIK_URL or COMET_URL_OVERRIDE is required to call Opik REST")
    return base, api_key, workspace


def opik_rest_base(settings: Settings) -> str | None:
    """Resolve Opik's REST API base URL from settings, or ``None`` if unconfigured.

    Single source of truth for the rule: an explicit ``OPIK_URL`` override wins;
    otherwise derive from ``COMET_URL_OVERRIDE + "/opik/api"``. Shared by
    ``resolve_opik_config`` (which treats ``None`` as a fatal misconfig) and
    ``identity.oauth.introspect_oauth_token`` (which treats ``None`` as "skip,
    fall back to the static workspace"), so both agree on where Opik lives.
    """
    if settings.opik_url:
        return settings.opik_url.rstrip("/")
    if settings.comet_url_override:
        return f"{settings.comet_url_override.rstrip('/')}/opik/api"
    return None


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


def _raise_for_status(resp: httpx.Response, entity_hint: str) -> None:
    """One sentence per status: what was asked and what to change.

    The backend's body and the REST path stay out: the body is untrusted text
    that can carry anything the request did, and the path is not a name the
    caller can use.
    """
    status = resp.status_code
    if 200 <= status < 300:
        return
    if status == 401:
        hint = note_backend_401() or "Check OPIK_API_KEY and OPIK_WORKSPACE."
        raise OpikAuthError(f"Opik rejected the credential for {entity_hint} (401). {hint}")
    if status == 403:
        raise OpikPermissionError(
            f"Permission denied for {entity_hint} (403). Use a credential for the "
            "workspace that owns it."
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
            status=status,
            backend_reason=reason,
        )
    if status >= 500:
        raise OpikServerError(
            f"Opik server error ({status}) for {entity_hint}. Retry the same call."
        )
    # 3xx / unexpected 2xx are already handled by the caller.
    raise OpikServerError(f"Unexpected status {status} for {entity_hint}.")

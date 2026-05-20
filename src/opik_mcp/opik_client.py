"""Thin async wrapper around Opik's REST API.

Read methods map 1:1 to a single REST endpoint and are consumed by the
``read`` / ``list`` registry. Writes go through the universal ``write``
tool's dispatcher (``writes/dispatch.py``) which calls
``OpikClient.write_json`` directly with templated paths and pre-built
bodies — no per-endpoint helper. Workspace is bound at construction time
and sent on every request via the ``Comet-Workspace`` header; the MCP
tool surface never takes a workspace argument (see design.md §1.5
"Scoping").
"""

from __future__ import annotations

import json as _json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal, Protocol

import httpx

from opik_mcp.config import MissingConfigError, Settings

# --- errors --------------------------------------------------------------- #


class OpikAuthError(RuntimeError):
    """Opik rejected the API key or workspace (401/403)."""


class OpikNotFoundError(RuntimeError):
    """Target entity does not exist (404). Wraps the entity hint."""


class OpikValidationError(RuntimeError):
    """Opik rejected the request body (400/422)."""


class OpikServerError(RuntimeError):
    """Opik returned a 5xx response."""


# --- types ---------------------------------------------------------------- #

FeedbackSource = Literal["sdk", "ui", "online_scoring"]
"""Mirrors ``com.comet.opik.api.ScoreSource``. The MCP server reports as ``sdk``."""


@dataclass(frozen=True)
class FeedbackScore:
    """Internal write shape mirroring opik-backend's ``FeedbackScore`` DTO.

    Not user-facing — the MCP tool layer builds this from its own params.
    """

    name: str
    value: float
    source: FeedbackSource = "sdk"
    category_name: str | None = None
    reason: str | None = None


class OpikListClient(Protocol):
    """Structural type for the list endpoints the ``list`` tool depends on.

    Defined here so test fakes (and the read/list registry) can depend on the
    Protocol instead of the concrete client — no ``cast(OpikClient, fake)``
    gymnastics in unit tests, and the registry stays decoupled from the HTTP
    implementation.
    """

    async def list_projects(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_traces(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_spans(
        self,
        *,
        trace_id: str,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 100,
    ) -> dict[str, Any]: ...

    async def list_test_suites(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_test_suite_items(
        self, test_suite_id: str, /, *, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_experiments(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_prompts(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_prompt_versions(
        self, prompt_id: str, /, *, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...


class OpikReadClient(OpikListClient, Protocol):
    """Adds singleton ``get_*`` endpoints to ``OpikListClient`` for the read tool.

    The read tool calls both shapes: singletons via ``get_*`` and search/
    composite reads via ``list_*`` (e.g. name-lookup, ``list_spans`` while
    inlining a trace's spans tree).
    """

    async def get_project(self, project_id: str, /) -> dict[str, Any]: ...

    async def get_trace(self, trace_id: str, /) -> dict[str, Any]: ...

    async def get_span(self, span_id: str, /) -> dict[str, Any]: ...

    async def get_test_suite(self, test_suite_id: str, /) -> dict[str, Any]: ...

    async def get_experiment(self, experiment_id: str, /) -> dict[str, Any]: ...

    async def get_prompt(self, prompt_id: str, /) -> dict[str, Any]: ...


# --- client --------------------------------------------------------------- #

_DEFAULT_TIMEOUT: Final = 30.0


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


class OpikClient:
    """Async HTTP client for Opik's ``/v1/private/...`` endpoints.

    Workspace is constructor-bound — the MCP tool layer never passes it.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        workspace: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._workspace = workspace
        self._client = client
        self._timeout = timeout

    # -- feedback scores --

    async def add_trace_feedback_score(self, trace_id: str, score: FeedbackScore) -> None:
        """``PUT /v1/private/traces/{id}/feedback-scores`` — single score on a trace."""
        await self._request(
            "PUT",
            f"/v1/private/traces/{trace_id}/feedback-scores",
            json=_score_body(score),
            expected_status=204,
            entity_hint=f"trace {trace_id!r}",
        )

    async def add_span_feedback_score(self, span_id: str, score: FeedbackScore) -> None:
        """``PUT /v1/private/spans/{id}/feedback-scores`` — single score on a span."""
        await self._request(
            "PUT",
            f"/v1/private/spans/{span_id}/feedback-scores",
            json=_score_body(score),
            expected_status=204,
            entity_hint=f"span {span_id!r}",
        )

    async def add_thread_feedback_score(
        self,
        thread_id: str,
        score: FeedbackScore,
        *,
        project_name: str | None = None,
    ) -> None:
        """``PUT /v1/private/traces/threads/feedback-scores`` — batch-only endpoint.

        opik-backend exposes no single-item write for threads, so we send a
        ``scores: [...]`` envelope with one entry. ``project_name`` is optional
        (defaults to the workspace's default project server-side).
        """
        item: dict[str, Any] = {"thread_id": thread_id} | _score_body(score)
        if project_name is not None:
            item["project_name"] = project_name
        await self._request(
            "PUT",
            "/v1/private/traces/threads/feedback-scores",
            json={"scores": [item]},
            expected_status=204,
            entity_hint=f"thread {thread_id!r}",
        )

    # -- comments --

    async def add_trace_comment(self, trace_id: str, text: str) -> None:
        """``POST /v1/private/traces/{id}/comments``. Returns 201 with no body."""
        await self._request(
            "POST",
            f"/v1/private/traces/{trace_id}/comments",
            json={"text": text},
            expected_status=201,
            entity_hint=f"trace {trace_id!r}",
        )

    async def add_span_comment(self, span_id: str, text: str) -> None:
        """``POST /v1/private/spans/{id}/comments``. Returns 201 with no body."""
        await self._request(
            "POST",
            f"/v1/private/spans/{span_id}/comments",
            json={"text": text},
            expected_status=201,
            entity_hint=f"span {span_id!r}",
        )

    async def add_thread_comment(self, thread_id: str, text: str) -> None:
        """``POST /v1/private/traces/threads/{id}/comments``. ``{id}`` is the thread UUID."""
        await self._request(
            "POST",
            f"/v1/private/traces/threads/{thread_id}/comments",
            json={"text": text},
            expected_status=201,
            entity_hint=f"thread {thread_id!r}",
        )

    # -- reads: projects --

    async def list_projects(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/projects`` — Spring Page envelope ``{content,page,size,total}``.

        ``name`` is a substring filter (case-insensitive on opik-backend) used
        for the read tool's name-lookup path.
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        return await self._get_json(
            "/v1/private/projects",
            params=params,
            entity_hint="projects",
        )

    async def get_project(self, project_id: str) -> dict[str, Any]:
        """``GET /v1/private/projects/{id}`` — single project record."""
        return await self._get_json(
            f"/v1/private/projects/{project_id}",
            params=None,
            entity_hint=f"project {project_id!r}",
        )

    # -- reads: traces / spans --

    async def list_traces(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/traces`` — requires ``project_id`` or ``project_name``."""
        if project_id is None and project_name is None:
            raise ValueError("list_traces requires project_id or project_name")
        params: dict[str, Any] = {"page": page, "size": size}
        if project_id is not None:
            params["project_id"] = project_id
        if project_name is not None:
            params["project_name"] = project_name
        return await self._get_json("/v1/private/traces", params=params, entity_hint="traces")

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        """``GET /v1/private/traces/{id}`` — trace metadata only (spans fetched separately)."""
        return await self._get_json(
            f"/v1/private/traces/{trace_id}",
            params=None,
            entity_hint=f"trace {trace_id!r}",
        )

    async def list_spans(
        self,
        *,
        trace_id: str,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 100,
    ) -> dict[str, Any]:
        """``GET /v1/private/spans?trace_id=...&project_id=...`` — spans on one trace.

        opik-backend rejects ``GET /v1/private/spans`` with 400 if neither
        ``project_id`` nor ``project_name`` is supplied (the spans index is
        sharded by project). Callers must thread one through; the resource
        layer extracts ``project_id`` from the trace record it just fetched.
        """
        if project_id is None and project_name is None:
            raise ValueError("list_spans requires project_id or project_name")
        params: dict[str, Any] = {"trace_id": trace_id, "page": page, "size": size}
        if project_id is not None:
            params["project_id"] = project_id
        if project_name is not None:
            params["project_name"] = project_name
        return await self._get_json(
            "/v1/private/spans",
            params=params,
            entity_hint=f"spans for trace {trace_id!r}",
        )

    async def get_span(self, span_id: str) -> dict[str, Any]:
        """``GET /v1/private/spans/{id}`` — single span."""
        return await self._get_json(
            f"/v1/private/spans/{span_id}",
            params=None,
            entity_hint=f"span {span_id!r}",
        )

    # -- reads: test suites (REST path = "datasets") --

    async def list_test_suites(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/datasets`` — Spring Page envelope.

        Opik 2.0 test suites share the dataset REST path. ``name`` is a
        substring filter used for name-lookup in the read tool.
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        return await self._get_json(
            "/v1/private/datasets",
            params=params,
            entity_hint="test_suites",
        )

    async def get_test_suite(self, test_suite_id: str) -> dict[str, Any]:
        """``GET /v1/private/datasets/{id}`` — Opik 2.0 test suites live on the dataset path."""
        return await self._get_json(
            f"/v1/private/datasets/{test_suite_id}",
            params=None,
            entity_hint=f"test_suite {test_suite_id!r}",
        )

    async def list_test_suite_items(
        self,
        test_suite_id: str,
        *,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/datasets/{id}/items`` — paginated item list."""
        return await self._get_json(
            f"/v1/private/datasets/{test_suite_id}/items",
            params={"page": page, "size": size},
            entity_hint=f"test_suite {test_suite_id!r} items",
        )

    # -- reads: experiments --

    async def list_experiments(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/experiments`` — Spring Page envelope."""
        params: dict[str, Any] = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        return await self._get_json(
            "/v1/private/experiments",
            params=params,
            entity_hint="experiments",
        )

    async def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        """``GET /v1/private/experiments/{id}``."""
        return await self._get_json(
            f"/v1/private/experiments/{experiment_id}",
            params=None,
            entity_hint=f"experiment {experiment_id!r}",
        )

    async def execute_experiment(self, body: dict[str, Any]) -> httpx.Response:
        """``POST /v1/private/experiments/execute`` — fire-and-return experiment run.

        opik-backend runs the experiment asynchronously. Returns 202 on accept
        with ``{experiments: [{experiment_id, prompt_index}], total_items}``.
        Like ``write_json``, this does NOT raise on 4xx/5xx — the orchestrator
        wraps non-2xx into ``OpikValidationError`` / ``OpikServerError``.
        """
        return await self.write_json(
            "POST",
            "/v1/private/experiments/execute",
            body,
        )

    # -- reads: prompts --

    async def list_prompts(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/prompts`` — Spring Page envelope."""
        params: dict[str, Any] = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        return await self._get_json(
            "/v1/private/prompts",
            params=params,
            entity_hint="prompts",
        )

    async def get_prompt(self, prompt_id: str) -> dict[str, Any]:
        """``GET /v1/private/prompts/{id}`` — singleton prompt record.

        opik-backend MAY include ``latestVersion`` inline but does not
        guarantee it (verified live on dev.comet.com: some prompts return
        without the field). Callers needing the full version history use
        ``list_prompt_versions`` — that's the single source of truth.
        """
        return await self._get_json(
            f"/v1/private/prompts/{prompt_id}",
            params=None,
            entity_hint=f"prompt {prompt_id!r}",
        )

    async def list_prompt_versions(
        self,
        prompt_id: str,
        *,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/prompts/{id}/versions`` — full version history."""
        return await self._get_json(
            f"/v1/private/prompts/{prompt_id}/versions",
            params={"page": page, "size": size},
            entity_hint=f"prompt {prompt_id!r} versions",
        )

    # -- internals --

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "Comet-Workspace": self._workspace,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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
        params: dict[str, Any] | None,
        entity_hint: str,
    ) -> dict[str, Any]:
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
                f"Unexpected status {resp.status_code} from GET {path} (expected 200)"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise OpikServerError(
                f"Opik returned non-JSON body for GET {path}: {resp.text[:200]!r}"
            ) from exc
        if not isinstance(body, dict):
            raise OpikServerError(
                f"Opik returned non-object JSON for GET {path}: {type(body).__name__}"
            )
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any],
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
                f"Unexpected status {resp.status_code} from {method} {path} "
                f"(expected {expected_status})"
            )
        return resp

    async def write_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | list[Any],
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


def resolve_opik_config(settings: Settings) -> tuple[str, str, str]:
    """Resolve ``(opik_base_url, api_key, workspace)`` from settings or raise.

    Centralizes the rule for deriving Opik's REST base from either an explicit
    ``OPIK_URL`` override or ``COMET_URL_OVERRIDE + "/opik/api"``. Both the
    score/comment orchestrator and the resource layer call this so the config
    contract lives in exactly one place.
    """
    if not settings.opik_api_key:
        raise MissingConfigError("OPIK_API_KEY is required to call Opik REST")
    if not settings.comet_workspace:
        raise MissingConfigError("COMET_WORKSPACE is required to call Opik REST")
    if settings.opik_url:
        base = settings.opik_url.rstrip("/")
    else:
        # ``comet_url_override`` has a non-empty default in ``Settings`` but
        # ``COMET_URL_OVERRIDE=""`` would override it to empty — defend against
        # that so we never POST to ``/opik/api`` (relative URL → wherever the
        # process happens to be).
        if not settings.comet_url_override:
            raise MissingConfigError("OPIK_URL or COMET_URL_OVERRIDE is required to call Opik REST")
        base = f"{settings.comet_url_override.rstrip('/')}/opik/api"
    return base, settings.opik_api_key, settings.comet_workspace


def make_opik_client(settings: Settings) -> OpikClient:
    """Construct an ``OpikClient`` bound to the configured workspace."""
    base_url, api_key, workspace = resolve_opik_config(settings)
    return OpikClient(base_url=base_url, api_key=api_key, workspace=workspace)


def _score_body(score: FeedbackScore) -> dict[str, Any]:
    """FeedbackScore → JSON body with ``None`` fields stripped."""
    return _drop_none(asdict(score))


def _raise_for_status(resp: httpx.Response, entity_hint: str) -> None:
    status = resp.status_code
    if 200 <= status < 300:
        return
    detail = _error_detail(resp)
    suffix = f" — {detail}" if detail else ""
    if status in (401, 403):
        raise OpikAuthError(
            f"Opik rejected the request ({status}). Check OPIK_API_KEY and COMET_WORKSPACE.{suffix}"
        )
    if status == 404:
        raise OpikNotFoundError(f"{entity_hint} not found (404).{suffix}")
    if status in (400, 422):
        raise OpikValidationError(
            f"Opik rejected the request body ({status}) for {entity_hint}.{suffix}"
        )
    if status >= 500:
        raise OpikServerError(f"Opik server error ({status}) for {entity_hint}.{suffix}")
    # 3xx / unexpected 2xx are already handled by the caller.
    raise OpikServerError(f"Unexpected status {status} for {entity_hint}.{suffix}")


def _error_detail(resp: httpx.Response) -> str:
    """Best-effort extraction of an error message from an Opik response body."""
    try:
        body = resp.json()
    except ValueError:
        text = resp.text[:200].replace("\n", " ").strip()
        return text
    if isinstance(body, dict):
        for key in ("message", "errors", "error"):
            if body.get(key):
                return str(body[key])[:200]
    return str(body)[:200]

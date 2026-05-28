import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import anyio
import httpx
from httpx_sse import aconnect_sse

from opik_mcp.error_kinds import ErrorKind


class PodNotReadyError(RuntimeError):
    """Pod did not become ready within the configured timeout."""

    error_kind: ClassVar[ErrorKind] = "pod_not_ready"
    http_status: ClassVar[int | None] = None


class OllieAuthError(RuntimeError):
    """Pod rejected the PPAUTH cookie.

    Distinct bucket from the Comet-layer ``CometAuthError``: a PPAUTH 401
    on the pod typically means the cookie expired between ``discover_pod``
    and the next pod call, while a Comet 401 means the OPIK_API_KEY itself
    is bad. Dashboards page these differently.
    """

    error_kind: ClassVar[ErrorKind] = "pod_auth"
    http_status: ClassVar[int | None] = None


class OllieStreamError(RuntimeError):
    """Generic Ollie-stream failure — our own protocol-drift signal.

    ``upstream_code`` carries the optional structured ``code`` from the SSE
    error frame (e.g. ``"rate_limited"``, ``"model_unavailable"``) so
    analytics can group failures without sniffing the human-readable
    message. ``None`` when the frame omitted it or when the error was
    raised from an internal control-flow path (confirm-POST failure, etc.)
    rather than a server-side error frame.

    Bucketed as ``"stream_protocol"`` at the bare leaf. Subclasses below
    pin specific raise sites to their own buckets (``session_evicted``,
    ``stream_idle``, ``stream_error_frame``, ``confirm_failed``, …). The
    bare class itself stays a pure wrapper: when raised ``from`` an
    upstream cause, ``unwrap_to_real_cause`` walks past it to the cause's
    bucket; when raised bare, it surfaces as ``stream_protocol``.
    """

    error_kind: ClassVar[ErrorKind] = "stream_protocol"
    http_status: ClassVar[int | None] = None

    def __init__(self, message: str, *, upstream_code: str | None = None) -> None:
        super().__init__(message)
        self.upstream_code = upstream_code


class PodSessionCreateError(OllieStreamError):
    """POST /sessions returned a body without a session_id — protocol drift
    on the pod side. Surfaced from ``OllieClient.create_session``."""

    error_kind: ClassVar[ErrorKind] = "session_create_failed"


class PodSessionLostError(OllieStreamError):
    """GET /sessions/{id}/stream returned 404 — session was evicted between
    create and stream connect. Surfaced from ``OllieClient.stream_events``."""

    error_kind: ClassVar[ErrorKind] = "session_evicted"


class PodErrorEventError(OllieStreamError):
    """Pod emitted an SSE ``error`` frame mid-stream. Carries the optional
    ``upstream_code`` from the frame."""

    error_kind: ClassVar[ErrorKind] = "stream_error_frame"


class PodStreamIdleError(OllieStreamError):
    """Heartbeat watchdog tripped — no SSE event for longer than the
    configured idle threshold. Surfaced from ``ask_ollie.heartbeat_loop``."""

    error_kind: ClassVar[ErrorKind] = "stream_idle"


class ConfirmDeclinedError(OllieStreamError):
    """User declined the elicit prompt (auto-approve disabled). Surfaced
    from ``ask_ollie`` when the user chooses not to approve a confirm."""

    error_kind: ClassVar[ErrorKind] = "cancelled"


class ConfirmPostError(OllieStreamError):
    """POST /sessions/{id}/confirm raised — audit row already written, but
    the pod won't make forward progress. Wraps the underlying transport
    error via ``__cause__``.

    NOT treated as a pure wrapper by the analytics unwrap (see
    ``analytics/errors._is_wrapper_exception``): we deliberately want the
    ``confirm_failed`` bucket to win even when there's a chained httpx
    cause. The cause class still surfaces via ``cause_type``.
    """

    error_kind: ClassVar[ErrorKind] = "confirm_failed"


@dataclass
class SSEEvent:
    """Envelope as it appears on the wire.

    `data` is the JSON object parsed from the SSE `data:` field. For pod
    events that means `{"parent_id": str | None, "payload": {...}}`. Callers
    that want the event-specific fields should read `data["payload"]`.
    """

    event: str
    data: dict[str, Any]


OnTick = Callable[[float], Awaitable[None]]
Sleeper = Callable[[float], Awaitable[None]]


class OllieClient:
    def __init__(
        self,
        *,
        ready_timeout_s: float = 120.0,
        ready_interval_s: float = 2.0,
        sleeper: Sleeper = anyio.sleep,
    ) -> None:
        self._timeout_s = ready_timeout_s
        self._interval_s = ready_interval_s
        self._sleeper = sleeper

    def _headers(self, ppauth: str, workspace: str | None = None) -> dict[str, str]:
        # PPAUTH cookie satisfies the pod's nginx auth gate. sessionToken is
        # pass-through into the FastAPI app's UserContext (hashed → user_id);
        # without it the pod sees every MCP call as user_id="anonymous".
        # Comet-Workspace header scopes the request to a workspace.
        headers = {"Cookie": f"PPAUTH={ppauth}; sessionToken={ppauth}"}
        if workspace is not None:
            headers["Comet-Workspace"] = workspace
        return headers

    async def wait_ready(
        self,
        compute_url: str,
        ppauth: str,
        *,
        on_tick: OnTick | None = None,
    ) -> None:
        url = f"{compute_url}/health/ready"
        elapsed = 0.0
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                if elapsed >= self._timeout_s:
                    raise PodNotReadyError(
                        f"Pod did not become ready within {self._timeout_s:.0f}s."
                    )
                try:
                    resp = await client.get(url, headers=self._headers(ppauth))
                    if resp.status_code == 200:
                        try:
                            body = resp.json()
                        except ValueError:
                            body = None
                        if isinstance(body, dict) and body.get("status") == "ok":
                            return
                    elif resp.status_code in (401, 403):
                        raise OllieAuthError(f"Pod rejected PPAUTH ({resp.status_code}).")
                except httpx.TransportError:
                    # Pod cold-start: DNS/TCP not up yet → ConnectError /
                    # ConnectTimeout; pod accepting but slow to first byte →
                    # ReadTimeout. TransportError covers all of these.
                    pass

                if on_tick is not None:
                    await on_tick(elapsed)
                await self._sleeper(self._interval_s)
                elapsed += self._interval_s

    async def create_session(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        body: dict[str, Any],
    ) -> str:
        """POST /sessions — pod queues the message and returns its session_id."""
        url = f"{compute_url}/sessions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(ppauth, workspace),
                json=body,
            )
            if resp.status_code in (401, 403):
                raise OllieAuthError(f"Pod rejected /sessions ({resp.status_code}).")
            resp.raise_for_status()
            data = resp.json()
            sid = data.get("session_id") if isinstance(data, dict) else None
            if not isinstance(sid, str) or not sid:
                raise PodSessionCreateError(f"POST /sessions returned no session_id: {data!r}")
            return sid

    async def stream_events(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """GET /sessions/{id}/stream — tail the session event log as SSE."""
        url = f"{compute_url}/sessions/{session_id}/stream"
        headers = {
            **self._headers(ppauth, workspace),
            "Accept": "text/event-stream",
        }
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            aconnect_sse(client, "GET", url, headers=headers) as event_source,
        ):
            status = event_source.response.status_code
            if status in (401, 403):
                raise OllieAuthError(f"Pod rejected /sessions/.../stream ({status}).")
            if status == 404:
                raise PodSessionLostError(
                    f"Session {session_id!r} not found on pod — "
                    "likely evicted between create_session and stream_events."
                )
            event_source.response.raise_for_status()
            async for sse in event_source.aiter_sse():
                try:
                    data: dict[str, Any] = json.loads(sse.data) if sse.data else {}
                except json.JSONDecodeError:
                    data = {"raw": sse.data}
                yield SSEEvent(event=sse.event or "message", data=data)

    async def confirm_session(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        tool_use_id: str,
        decision: str,
    ) -> None:
        """POST /sessions/{id}/confirm — resolve a pending tool gate."""
        url = f"{compute_url}/sessions/{session_id}/confirm"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(ppauth, workspace),
                json={"tool_use_id": tool_use_id, "decision": decision},
            )
            if resp.status_code in (401, 403):
                raise OllieAuthError(f"Pod rejected /confirm ({resp.status_code}).")
            resp.raise_for_status()

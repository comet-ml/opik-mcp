"""The assembled ``OpikClient`` and the factory that builds one per call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from opik_mcp.client.annotations import AnnotationEndpoints
from opik_mcp.client.base import _DEFAULT_TIMEOUT, resolve_opik_config
from opik_mcp.client.dataset import DatasetEndpoints
from opik_mcp.client.diagnostics import DiagnosticsEndpoints
from opik_mcp.client.experiment import ExperimentEndpoints
from opik_mcp.client.observability import ObservabilityEndpoints
from opik_mcp.client.project import ProjectEndpoints
from opik_mcp.client.prompt import PromptEndpoints
from opik_mcp.config import Settings, get_settings


class OpikClient(
    AnnotationEndpoints,
    ProjectEndpoints,
    ObservabilityEndpoints,
    DatasetEndpoints,
    ExperimentEndpoints,
    PromptEndpoints,
    DiagnosticsEndpoints,
):
    """Async HTTP client for Opik's ``/v1/private/...`` endpoints.

    Workspace is constructor-bound — the MCP tool layer never passes it.
    """


def make_opik_client(
    settings: Settings,
    *,
    timeout: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> OpikClient:
    """Construct an ``OpikClient`` bound to the configured workspace.

    ``timeout`` overrides the default per-request timeout; the ``list`` tool
    passes a longer one for free-text ``search``, which the backend can take
    over 30 s to answer on a cold cache.

    ``http_client`` is the connection every request on this client will ride.
    Prefer :func:`client_for_call`, which owns one for the span of a tool
    call; pass it here directly only when the caller already has one whose
    lifetime it manages (a long-lived hosted process, a test).
    """
    base_url, api_key, workspace = resolve_opik_config(settings)
    return OpikClient(
        base_url=base_url,
        api_key=api_key,
        workspace=workspace,
        client=http_client,
        timeout=_DEFAULT_TIMEOUT if timeout is None else timeout,
    )


@asynccontextmanager
async def client_for_call[C](
    settings: Settings | None,
    supplied: C | None,
    *,
    timeout: float | None = None,
) -> AsyncIterator[C | OpikClient]:
    """The client one tool call should use: the caller's, or one we own.

    Every tool entry point needs the same two-branch lifecycle — use an
    injected client untouched, or open one and close it on the way out,
    including on a raised error. Written out at each entry point it was three
    copies of an ``AsyncExitStack`` and the same comment; as a context manager
    the stack disappears from all three.

    A supplied client is yielded as-is and never closed: its owner may be
    reusing it across many calls, and closing it would break the next one.

    The one we own binds a single ``httpx.AsyncClient`` for the span of the
    call, so every request in it shares a connection. Without that, ``_http``
    opens a fresh client — and a fresh pool — per request, and a composite
    read pays a TCP + TLS handshake per backend call. Measured against
    www.comet.com: 259 ms per extra request (six sequential GETs, 2648 ms with
    a client each, 1355 ms with one shared). It is closed when the block
    exits, so nothing survives into the next tool call.

    The SDK's documented home for a shared client is the server lifespan (one
    per process), which would also spare the handshake *between* calls;
    ``make_opik_client(http_client=…)`` is the seam for that. Per-call is the
    conservative half: no process-wide state, no client outliving the request
    that made it.
    """
    if supplied is not None:
        yield supplied
        return
    limit = _DEFAULT_TIMEOUT if timeout is None else timeout
    async with httpx.AsyncClient(timeout=limit) as http:
        yield make_opik_client(settings or get_settings(), timeout=timeout, http_client=http)

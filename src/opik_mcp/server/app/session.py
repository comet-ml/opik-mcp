"""Per-session hooks on the lowlevel MCP server: instructions and request auth."""

from __future__ import annotations

import logging
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import request_ctx
from mcp.server.models import InitializationOptions
from mcp.types import CallToolRequest, ServerResult
from starlette.requests import Request

from opik_mcp.identity.context import inbound_authorization, inbound_workspace
from opik_mcp.instructions import render_instructions

logger = logging.getLogger("opik_mcp")


def install_session_instructions(server: FastMCP[object]) -> None:
    """Render ``InitializeResult.instructions`` per session rather than once at boot.

    FastMCP captures the ``instructions`` string at construction time, so the blob
    would be identical for every session and could never name the per-session
    OAuth workspace. We wrap the lowlevel server's ``create_initialization_options``
    (invoked once per session, inside the session task that inherits the
    ``initialize`` request's ContextVars) to re-render the blob with the workspace
    resolved for that session. Mirrors ``install_tools_listed_emitter``'s in-place
    handler swap. The boot-time static render stays on the options as a fallback.
    """
    try:
        lowlevel = server._mcp_server
    except AttributeError:
        logger.debug("install_session_instructions: mcp has no _mcp_server attribute")
        return
    lowlevel.create_initialization_options = _rendering_instructions(  # type: ignore[method-assign]
        lowlevel.create_initialization_options
    )


def _rendering_instructions[**P](
    original: Callable[P, InitializationOptions],
) -> Callable[P, InitializationOptions]:
    def create_initialization_options(*args: P.args, **kwargs: P.kwargs) -> InitializationOptions:
        options = original(*args, **kwargs)
        try:
            options.instructions = render_instructions()
        except Exception:
            # A render hiccup must never break the initialize handshake — leave
            # the boot-time static instructions already on the options in place.
            logger.debug("per-session instructions render failed", exc_info=True)
        return options

    return create_initialization_options


def _current_http_request() -> Request | None:
    """The HTTP request behind the MCP request being handled, or ``None`` (stdio)."""
    try:
        request = request_ctx.get().request
    except (LookupError, AttributeError):
        return None
    return request if isinstance(request, Request) else None


def install_request_auth_rebinding(server: FastMCP[object]) -> None:
    """Forward the bearer of the CURRENT request on ``tools/call``, not the handshake's.

    ``BearerAuthMiddleware`` sets the inbound-auth ContextVars on the request
    task, but a tool runs in the MCP session task, which the SDK forks from the
    ``initialize`` request — so inside a tool those vars still hold the
    handshake-time values. Harmless while a bearer never changes during a
    session; fatal once it does: after the ``invalid_token`` 401 (OPIK-8252)
    the host refreshes and re-sends with a NEW access token, the middleware
    validates that one, and the outbound client would forward the OLD, dead
    one — every call after a refresh meets the backend's 401 and the connector
    never recovers, which is exactly the failure the 401 exists to fix.

    The SDK attaches the Starlette request of each ``tools/call`` to its
    request context and handles each message in its own task, so re-binding
    the vars there is both current and isolated. stdio has no request and is
    left untouched. Mirrors ``install_tools_listed_emitter``'s in-place swap.
    """
    try:
        lowlevel = server._mcp_server
    except AttributeError:
        logger.debug("install_request_auth_rebinding: mcp has no _mcp_server attribute")
        return
    original = lowlevel.request_handlers.get(CallToolRequest)
    if original is None:
        logger.debug("install_request_auth_rebinding: no CallToolRequest handler registered")
        return

    async def wrapped(req: CallToolRequest) -> ServerResult:
        request = _current_http_request()
        if request is None:
            return await original(req)
        auth = request.headers.get("authorization")
        if not auth:
            # Cannot happen behind BearerAuthMiddleware (it 401s first); if it
            # ever does, leave the vars as the middleware set them.
            return await original(req)
        auth_token = inbound_authorization.set(auth)
        workspace_token = inbound_workspace.set(request.headers.get("comet-workspace"))
        try:
            return await original(req)
        finally:
            inbound_workspace.reset(workspace_token)
            inbound_authorization.reset(auth_token)

    lowlevel.request_handlers[CallToolRequest] = wrapped

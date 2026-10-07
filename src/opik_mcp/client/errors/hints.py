"""What an error tells the caller to change, from the settings this call used."""

from __future__ import annotations

from typing import Final

import httpx

from opik_mcp.config import (
    DOCS_HOSTED_SERVER,
    DOCS_LOCAL_SERVER,
    get_settings,
    opik_rest_base,
)
from opik_mcp.identity.context import inbound_authorization

#: This machine: a call there that gets no answer usually means Opik is not running.
LOOPBACK_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


def credential_hint() -> str:
    """What to change after a 401 that no OAuth bearer explains.

    With no key sent to Opik Cloud: a key, or the hosted server, which needs
    none. The client reads the server's env only when it starts.
    """
    settings = get_settings()
    host = httpx.URL(opik_rest_base(settings) or "").host
    cloud = host == "comet.com" or host.endswith(".comet.com")
    if inbound_authorization.get() or settings.opik_api_key or not cloud:
        return f"Check OPIK_API_KEY and OPIK_WORKSPACE: {DOCS_LOCAL_SERVER}"
    return (
        f"No API key is set: put one from https://{host}/api/my/settings/ in "
        "OPIK_API_KEY, or use the hosted server, which needs none: "
        f"https://{host}/opik/api/v1/mcp (setup: {DOCS_HOSTED_SERVER}). Restart the "
        "MCP client after either."
    )


def unreachable(err: httpx.HTTPError) -> str:
    """Why a call got no answer and, on a local server, where it went (without
    any user:password) and whether Opik is running there."""
    reason = str(err) or type(err).__name__
    if inbound_authorization.get():
        # The hosted server: its backend address is not the caller's to fix.
        return reason
    base = httpx.URL(opik_rest_base(get_settings()) or "")
    text = f"{reason} (tried {base.copy_with(username=None, password=None)})"
    if base.host in LOOPBACK_HOSTS:
        text += ". Is Opik running? Open-source Opik serves its API at http://localhost:5173/api"
    return f"{text}. Setup: {DOCS_LOCAL_SERVER}"

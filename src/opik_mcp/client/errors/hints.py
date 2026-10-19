"""What an error tells the caller to change, from the settings this call used."""

from __future__ import annotations

import ssl
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


def address(url: str) -> str:
    """``url`` without any user:password, for a message to name."""
    return str(httpx.URL(url).copy_with(username=None, password=None))


def no_api_at(base_url: str) -> str:
    """An address that answered, but not as Opik's API: the two paths it serves on."""
    url = httpx.URL(address(base_url))
    host = f"{url.scheme}://{url.netloc.decode()}"
    return (
        f"No Opik API at {address(base_url)}. Open-source Opik serves it at {host}/api "
        f"(set OPIK_URL); a Comet platform at {host}/opik/api (set "
        f"COMET_URL_OVERRIDE={host}). Setup: {DOCS_LOCAL_SERVER}"
    )


def unreachable(err: httpx.HTTPError) -> str:
    """Why a call got no answer and, on a local server, where it went (without
    any user:password) and whether Opik is running there."""
    reason = str(err) or type(err).__name__
    if inbound_authorization.get():
        # The hosted server: its backend address is not the caller's to fix.
        return reason
    base = opik_rest_base(get_settings()) or ""
    if _is_certificate_rejected(err):
        # The raw ssl text names the failure but not the fix. Never suggest
        # turning verification off.
        return (
            f"the TLS certificate was not trusted (tried {address(base)}). A proxy or a "
            "self-signed certificate is in the chain: set SSL_CERT_FILE=/path/to/ca.pem in "
            "the MCP server's env block and restart the MCP client."
        )
    text = f"{reason} (tried {address(base)})"
    if httpx.URL(base).host in LOOPBACK_HOSTS:
        text += ". Is Opik running? Open-source Opik serves its API at http://localhost:5173/api"
    return f"{text}. Setup: {DOCS_LOCAL_SERVER}"


def _is_certificate_rejected(err: httpx.HTTPError) -> bool:
    """httpx wraps the ``ssl`` error twice (httpcore, then httpx), so look down the chain."""
    seen: set[int] = set()
    cause: BaseException | None = err
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return True
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(err)

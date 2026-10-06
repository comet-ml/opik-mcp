"""What a failed call tells the agent about the server's own setup.

A broken setup reaches the agent as one tool error, usually on its first call,
and that error is all it has: it cannot see the server's env, its log or the
URL it calls. So each message here names the URL that was tried, the likely
cause and the setting that fixes it, and says that the MCP client reads env
only at startup, the step an agent misses after editing a config file.

URLs and env advice are given only when the call runs on the server's own env:
no inbound credential, which in practice means stdio, since the HTTP server
requires a bearer. A hosted server's backend URL is neither the caller's to
change nor to see.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from opik_mcp.config import Settings, destination_kind, looks_unsubstituted
from opik_mcp.identity.context import inbound_authorization

#: Where open-source Opik serves its REST API on the machine it runs on.
OPEN_SOURCE_LOCAL_API = "http://localhost:5173/api"
#: Opik Cloud's API key page, and the hosted server that signs in without a key.
CLOUD_KEY_PAGE = "https://www.comet.com/api/my/settings/"
HOSTED_SERVER_URL = "https://www.comet.com/opik/api/v1/mcp"
RESTART = "Env changes take effect only after the MCP client restarts."


def redact_url(url: str) -> str:
    """``url`` without a ``user:password@``, query or fragment."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def own_env() -> bool:
    """Whether this call runs on the server's own env, not a caller's credential."""
    return not inbound_authorization.get()


def _request_of(resp: httpx.Response) -> httpx.Request | None:
    try:
        return resp.request
    except RuntimeError:
        # A response built by hand, as tests do, has no request to inspect.
        return None


def unreachable(what: str, err: httpx.HTTPError, base_url: str | None) -> str:
    """A connection that failed: where it went, why, and what to check."""
    reason = (str(err) or type(err).__name__).rstrip(".")
    if base_url is None or not own_env():
        return f"Could not reach Opik to {what}: {reason}"
    url = redact_url(base_url).rstrip("/")
    if destination_kind(url) == "local":
        check = "Is Opik running?"
        if url != OPEN_SOURCE_LOCAL_API:
            check += (
                f" Open-source Opik serves its API at {OPEN_SOURCE_LOCAL_API} by default; "
                "set OPIK_URL to it."
            )
    else:
        check = (
            "Check the host in OPIK_URL (or COMET_URL_OVERRIDE) and that this machine can reach it."
        )
    return f"Could not reach Opik at {url} to {what}: {reason}. {check} {RESTART}"


def credential_hint(resp: httpx.Response) -> str | None:
    """After a 401 on the server's own env: what is wrong with the key, if anything shows."""
    request = _request_of(resp)
    if request is None or not own_env():
        return None
    sent = request.headers.get("authorization")
    if not sent:
        if destination_kind(str(request.url)) == "cloud":
            where = (
                f"create one at {CLOUD_KEY_PAGE}, or use the hosted server "
                f"{HOSTED_SERVER_URL}, which signs in without a key"
            )
        else:
            where = "use a key from this Comet deployment"
        return f"No API key was sent: set OPIK_API_KEY ({where}). {RESTART}"
    if looks_unsubstituted(sent):
        return (
            "OPIK_API_KEY holds an unfilled placeholder, not a key: put the key itself "
            f"in the MCP client's config. {RESTART}"
        )
    parts = urlsplit(redact_url(str(request.url)))
    origin = f"{parts.scheme}://{parts.netloc}"
    return (
        f"Check OPIK_API_KEY and OPIK_WORKSPACE: the key must come from {origin} and "
        f"the workspace must be one it can use. {RESTART}"
    )


def status_hint(resp: httpx.Response) -> str | None:
    """After a 403 or a 5xx on the server's own env: the setting or service to check."""
    request = _request_of(resp)
    if request is None or not own_env():
        return None
    if resp.status_code == 403:
        workspace = request.headers.get("comet-workspace")
        return (
            f"This call used workspace {workspace!r}; check OPIK_WORKSPACE." if workspace else None
        )
    if resp.status_code >= 500 and destination_kind(str(request.url)) != "cloud":
        return "If it persists, check that Opik's backend is running behind this URL."
    return None


def url_warnings(settings: Settings) -> list[str]:
    """Startup warnings for a URL setting that cannot reach Opik."""
    override = settings.comet_url_override
    if settings.opik_url or not override or destination_kind(override) != "local":
        return []
    url = redact_url(override).rstrip("/")
    return [
        f"COMET_URL_OVERRIDE={url} sends Opik calls to {url}/opik/api, the path of a "
        f"Comet deployment. Open-source Opik serves its API at {url}/api: set "
        f"OPIK_URL={url}/api instead."
    ]


__all__ = [
    "CLOUD_KEY_PAGE",
    "HOSTED_SERVER_URL",
    "OPEN_SOURCE_LOCAL_API",
    "RESTART",
    "credential_hint",
    "own_env",
    "redact_url",
    "status_hint",
    "unreachable",
    "url_warnings",
]

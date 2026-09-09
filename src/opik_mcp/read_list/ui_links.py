"""Where the Opik UI lives for this session — base URL and workspace name.

Two consumers must agree on this: the instructions blob, which tells the
agent the UI address once per session, and the links a ``read`` attaches to
an entity (a Diagnostics issue's page, a trace deep link) so the agent can
hand the user something clickable without guessing the URL shape. Keeping
the derivation here means neither can drift from where REST calls go.
"""

from __future__ import annotations

import base64

from opik_mcp.auth_context import (
    classify_bearer,
    inbound_authorization,
    inbound_workspace,
    resolved_workspace_name,
)
from opik_mcp.config import DEFAULT_WORKSPACE, Settings
from opik_mcp.opik_client import opik_rest_base


def opik_ui_base(settings: Settings) -> str | None:
    """Opik **UI** base URL, or ``None`` when Opik's location is unconfigured.

    Derived from :func:`opik_rest_base` — the single source of truth for where
    Opik lives (``OPIK_URL`` override, else ``COMET_URL_OVERRIDE + "/opik/api"``).
    That base is the REST **API** base (``…/opik/api``); the UI lives at the
    same origin without the trailing ``/api`` segment, so we strip it.
    """
    base = opik_rest_base(settings)
    if base is None:
        return None
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base


def current_workspace(settings: Settings) -> str:
    """Workspace name for THIS session, most to least authoritative: an explicit
    inbound ``Comet-Workspace`` header → the OAuth-introspected name → the
    static ``Settings`` workspace → ``"default"``."""
    return (
        inbound_workspace.get()
        or resolved_workspace_name.get()
        or settings.comet_workspace
        or DEFAULT_WORKSPACE
    )


def link_workspace(settings: Settings) -> str | None:
    """Workspace to build a **link** with, or ``None`` when it cannot be known.

    Same precedence as :func:`current_workspace`, with one difference: under an
    OAuth bearer the workspace is derived from the token server-side, so if
    neither the inbound header nor introspection named it, the static settings
    fallback would produce a link into the wrong workspace. The instructions
    blob can afford a best-effort name; a link cannot.
    """
    known = inbound_workspace.get() or resolved_workspace_name.get()
    if known:
        return known
    auth = inbound_authorization.get()
    if auth and classify_bearer(auth)[0] == "oauth":
        return None
    return settings.comet_workspace or DEFAULT_WORKSPACE


def trace_link_template(settings: Settings) -> str | None:
    """A clickable URL for any trace in this session, with ``{trace_id}`` left
    to fill in — or ``None`` when Opik's location cannot be known.

    Unlike :func:`project_page_url` this goes through opik-backend's redirect,
    which looks the trace up and derives both the project and the workspace
    from it. That costs a hop but buys two things the direct URL cannot: the
    agent needs no ``project_id`` (``list('trace', …)`` never returns one), and
    the link is still correct when the workspace is unknown, as it is under an
    OAuth bearer that introspection could not name.

    The ``path`` parameter carries the REST base back to the backend, which
    cuts it at ``/api`` to find the UI origin and rejects a base without that
    segment — so we return ``None`` rather than build a link it would refuse.
    Encoded url-safe and unpadded: the backend decodes with Java's URL decoder,
    and the value rides in a query string.
    """
    base = opik_rest_base(settings)
    if base is None or "/api" not in base:
        return None
    path = base64.urlsafe_b64encode(base.encode()).decode().rstrip("=")
    return f"{base}/v1/session/redirect/projects/?trace_id={{trace_id}}&path={path}"


def project_page_url(settings: Settings, project_id: str, page: str) -> str | None:
    """``<ui>/<workspace>/projects/<project_id>/<page>``, or ``None`` when the
    UI base or the workspace cannot be known for this session."""
    base = opik_ui_base(settings)
    workspace = link_workspace(settings)
    if base is None or workspace is None:
        return None
    return f"{base}/{workspace}/projects/{project_id}/{page}"


__all__ = [
    "current_workspace",
    "link_workspace",
    "opik_ui_base",
    "project_page_url",
    "trace_link_template",
]

"""Where the Opik UI lives for this session, and how to address a page on it.

Two consumers must agree on the first: the instructions blob, which tells the
agent the UI address once per session, and the links a ``read`` attaches to
an entity so the agent can hand the user something clickable without guessing
the URL shape. Keeping the derivation here means neither can drift from where
REST calls go.

THE HAZARD EVERY BUILDER BELOW IS SHAPED BY, stated once here rather than at
each of them. The UI is project-scoped: a page is ``/{workspace}/projects/
{projectId}/…``. It has been through one migration, and the paths it retired
are still reachable through a compatibility shim that fills the missing
project slot from whatever project the reader last had open. So a link in the
old shape does not fail. It opens a real page under someone else's project,
which is why this needs enforcing in code and not in review.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, get_args
from urllib.parse import quote

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


ProjectArea = Literal[
    "agent-playground",
    "alerts",
    "annotation-queues",
    "dashboards",
    "datasets",
    "diagnostics",
    "diagnostics/resolved",
    "experiments",
    "home",
    "logs",
    "ollie",
    "online-evaluation",
    "optimizations",
    "playground",
    "prompts",
    "test-suites",
]
"""The areas the Opik UI serves under ``/projects/{id}/``, and only those.

``traces`` is the subtle absence: it is still under ``/projects/{id}/`` and
looks current, but the router keeps it only to forward to ``logs``.

Typed as a ``Literal`` rather than checked only at runtime, so a link to a
page that does not exist fails at ``make typecheck`` instead of in an answer
a user is reading.
"""

_LIVE_AREAS: Final[frozenset[str]] = frozenset(get_args(ProjectArea))


@dataclass(frozen=True)
class ViewPage:
    """The page something with no page of its own is visible on.

    The label is not decoration: a link to Logs under a score name would
    otherwise read as a link to the score.
    """

    area: ProjectArea
    opens: str
    """What the page shows the reader, when the listing has rows."""
    opens_empty: str
    """The same, for an empty listing, where "this rule is a row" is false."""


def project_page_url(
    settings: Settings,
    project_id: str,
    area: ProjectArea,
    *,
    subpath: str | None = None,
    query: str | None = None,
) -> str | None:
    """``<ui>/<workspace>/projects/<project_id>/<area>[/<subpath>][?<query>]``,
    or ``None`` when the UI base or the workspace cannot be known.

    ``area`` names a page the UI serves; ``subpath`` is the id of the thing on
    it, for the areas whose route carries one; ``query`` is an already-formed
    query string, which the caller owns because some of them carry a template
    slot (``trace={trace_id}``) that must survive unencoded.

    The two failure modes are deliberately different. An unknown workspace is a
    fact about the session and yields ``None`` — no link beats a wrong one. An
    area outside the set is a bug in this repository, so it raises rather than
    quietly dropping a link nobody then notices is missing.
    """
    if area not in _LIVE_AREAS:
        raise ValueError(
            f"{area!r} is not an area the Opik UI serves under a project; "
            f"expected one of: {', '.join(sorted(_LIVE_AREAS))}"
        )
    base = opik_ui_base(settings)
    workspace = link_workspace(settings)
    if base is None or workspace is None or not project_id:
        return None
    path = f"{base}/{workspace}/projects/{project_id}/{area}"
    if subpath:
        path = f"{path}/{subpath}"
    return f"{path}?{query}" if query else path


def experiments_compare_url(
    settings: Settings,
    *,
    project_id: str,
    dataset_id: str,
    experiment_ids: Sequence[str],
) -> str | None:
    """The UI's compare view for these runs, or ``None`` when it cannot be known.

    One run and a pair build the same URL. Order is the caller's: the UI reads
    the first as the baseline.

    The compare view is keyed by the *dataset* and sits under the project the
    runs belong to; a run has no page of its own. Without the project this is
    one of the retired addresses the module docstring describes.
    """
    if not project_id or not dataset_id or not experiment_ids:
        return None
    runs = quote(json.dumps(list(experiment_ids), separators=(",", ":")))
    return project_page_url(
        settings,
        project_id,
        "experiments",
        subpath=f"{dataset_id}/compare",
        query=f"experiments={runs}",
    )


def scoped_entity_links(
    settings: Settings,
    record: dict[str, object],
    *,
    area: ProjectArea,
    noun: str,
) -> dict[str, str]:
    """A project-scoped entity's page, or the sentence that explains its absence.

    Shared by the two entities the backend lets exist without a project. When
    one does, there is no page for it anywhere — so this returns a reason in
    place of a url, which is the only honest answer and is also how the gap
    reaches whoever can close it.
    """
    entity_id = record.get("id")
    project_id = record.get("project_id")
    if not isinstance(entity_id, str) or not entity_id:
        return {}
    if not isinstance(project_id, str) or not project_id:
        return {
            "url_absent": (
                f"This {noun} is not scoped to a project, and the Opik UI addresses "
                f"{noun}s under one — so it has no page to open."
            )
        }
    url = project_page_url(settings, project_id, area, subpath=entity_id)
    return {"url": url} if url is not None else {}


def logs_page_url(
    settings: Settings, *, project_id: str, logs_type: str, **panels: str
) -> str | None:
    """The Logs page on one of its views, with the named panels open.

    ``panels`` become ``&<panel>=<value>`` in the order given: the view's
    record first, then a selection inside it. A value may be a template slot
    (``{id}``), which is why nothing here encodes it.
    """
    query = "&".join((f"logsType={logs_type}", *(f"{k}={v}" for k, v in panels.items())))
    return project_page_url(settings, project_id, "logs", query=query)


def view_link(
    settings: Settings,
    view_page: ViewPage,
    project_id: str,
    *,
    is_empty: bool = False,
) -> dict[str, str] | None:
    """Where to go and look at something that is not a page, or ``None``.

    A feedback score name is a column, an automation rule is a row, a metric
    is a chart. None of them has an address of its own and none ever will, so
    waiting for a deep link means these answers stay dead ends forever.

    The label travels with the url because the link overstates itself without
    one — and the agent is told never to print a bare URL, so the words it
    puts around the link should be the true ones rather than the entity's
    name.
    """
    if not project_id:
        return None
    url = project_page_url(settings, project_id, view_page.area)
    if url is None:
        return None
    # "where this rule is a row" is false of a page with no rows. The link is
    # still the right one — it is where the reader goes to make one — so the
    # sentence changes rather than the link disappearing.
    return {"url": url, "url_opens": view_page.opens_empty if is_empty else view_page.opens}


__all__ = [
    "ProjectArea",
    "ViewPage",
    "current_workspace",
    "experiments_compare_url",
    "link_workspace",
    "logs_page_url",
    "opik_ui_base",
    "project_page_url",
    "scoped_entity_links",
    "trace_link_template",
    "view_link",
]

"""Where the Opik UI lives for this session — base URL and workspace name.

Two consumers must agree on this: the instructions blob, which tells the
agent the UI address once per session, and the links a ``read`` attaches to
an entity (a Diagnostics issue's page, a trace deep link) so the agent can
hand the user something clickable without guessing the URL shape. Keeping
the derivation here means neither can drift from where REST calls go.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
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

Closed on purpose. The UI is project-scoped and has been through one migration
already: the paths it retired are still reachable through a compatibility shim
that fills the project slot from whatever the reader last had open, so a link
to one of them resolves somewhere plausible and wrong. ``traces`` is the
subtle member of that set — it is still under ``/projects/{id}/``, but the
router keeps it only to forward to ``logs``, so it is absent here too.

Typed as a ``Literal`` rather than checked only at runtime so that a link to a
page that does not exist fails at ``make typecheck``, where it costs nothing,
instead of in an answer a user is reading.
"""

_LIVE_AREAS: Final[frozenset[str]] = frozenset(get_args(ProjectArea))


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
    project_id: str,
    dataset_id: str,
    experiment_ids: Sequence[str],
) -> str | None:
    """The UI's compare view for these runs, or ``None`` when it cannot be known.

    One run and a pair build the same URL. Order is the caller's: the UI reads
    the first as the baseline.

    The compare view is keyed by the *dataset*, and sits under the project the
    runs belong to — a run has no page of its own. The project is not
    decoration: without it the address is one v2 retired, and the compatibility
    shim would resolve it against whatever project the reader last had open.
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


#: The entities that have no page of their own, the page each is visible on,
#: and what to look for there. The label is not decoration: a link to Logs
#: under a score name would otherwise read as a link to the score.
_VIEW_PAGES: Final[dict[str, tuple[ProjectArea, str, str]]] = {
    "score_name": (
        "logs",
        "the project's Logs, where this score is a column on the rows that carry it",
        "the project's Logs, where scores appear as columns once something is scored",
    ),
    "online_rule": (
        "online-evaluation",
        "the project's Online evaluation rules, where this rule is a row",
        "the project's Online evaluation, where a rule can be created",
    ),
    "project_metric": (
        "dashboards",
        "the project's Dashboards, where this metric is charted",
        "the project's Dashboards, where metrics are charted",
    ),
}


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


def view_link_note(
    settings: Settings,
    entity_type: str,
    project_id: str,
    *,
    empty: bool = False,
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
    page = _VIEW_PAGES.get(entity_type)
    if page is None or not project_id:
        return None
    area, opens, opens_empty = page
    url = project_page_url(settings, project_id, area)
    if url is None:
        return None
    # "where this rule is a row" is false of a page with no rows. The link is
    # still the right one — it is where the reader goes to make one — so the
    # sentence changes rather than the link disappearing.
    return {"url": url, "url_opens": opens_empty if empty else opens}


def thread_page_url(settings: Settings, project_id: str, thread_id: str) -> str | None:
    """The Logs page on the threads view, with this thread open."""
    if not thread_id:
        return None
    return project_page_url(
        settings, project_id, "logs", query=f"logsType=threads&thread={thread_id}"
    )


def trace_page_url(
    settings: Settings,
    project_id: str,
    trace_id: str,
    *,
    span_id: str | None = None,
) -> str | None:
    """The Logs page with this trace open, or ``None`` when it cannot be built.

    The direct address, for when the project and the workspace are both known.
    :func:`trace_link_template` is the fallback for when they are not — it
    costs a hop and lands on ``/traces``, which v2 keeps only to forward here.

    ``span_id`` selects one span inside the opened trace. It is not an address
    of its own: the UI treats it as panel state under the trace, and writes an
    empty one into the query when a trace is opened without a span.
    """
    if not trace_id:
        return None
    query = f"logsType=traces&trace={trace_id}"
    if span_id:
        query = f"{query}&span={span_id}"
    return project_page_url(settings, project_id, "logs", query=query)


__all__ = [
    "ProjectArea",
    "current_workspace",
    "experiments_compare_url",
    "link_workspace",
    "opik_ui_base",
    "project_page_url",
    "scoped_entity_links",
    "thread_page_url",
    "trace_link_template",
    "trace_page_url",
    "view_link_note",
]

"""The ``dashboard.*`` write operations — resolution and compilation.

The other write operations translate one validated model into one request
body. Dashboards need two things those do not, and both live here rather
than in ``writes/dispatch.py``:

*Resolution.* A chart is described in the words a user says — a project
NAME, a dashboard by its title — while the stored config holds UUIDs. So
the live path resolves names to ids first (:func:`resolve_context`), which
needs a client and therefore cannot happen during validation.

*Read-modify-write.* opik-backend stores ``config`` as one opaque
document and ``PATCH`` replaces it wholesale, so adding a chart means
fetching the current config, editing it, and sending the whole thing back.
Nothing here mutates what it fetched: a failed request leaves the live
dashboard exactly as it was.

``dry_run`` runs without a client, so it previews with an empty context and
:func:`preview_note` says which parts get resolved at execution — a preview
that quietly showed unresolved names as "no project" would be a preview of
a dashboard nobody asked for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opik_mcp.charts.config import add_chart, build_config, iter_widgets, remove_widget
from opik_mcp.charts.spec import ChartSpec
from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
    opik_rest_base,
)
from opik_mcp.writes.errors import BackendError, ValidationFailedError, ValidationIssue

if TYPE_CHECKING:  # pragma: no cover — import cycle at runtime, types only
    from opik_mcp.opik_client import OpikClient
    from opik_mcp.writes.registry import WriteOperation

DASHBOARDS_ENDPOINT = "/v1/private/dashboards"

#: Operations handled here. ``dispatch`` routes on this rather than on a
#: name prefix so a future ``dashboard.*`` operation cannot silently fall
#: into the generic builder and emit a body nobody wrote.
DASHBOARD_OPERATIONS: frozenset[str] = frozenset(
    {
        "dashboard.create",
        "dashboard.update",
        "dashboard.add_charts",
        "dashboard.remove_charts",
    }
)


def _fail(
    op: WriteOperation,
    field: str,
    message: str,
    code: str,
) -> ValidationFailedError:
    """A ``validation_failed`` envelope carrying this operation's schema.

    Resolution failures ARE validation failures from the caller's side —
    "no project called that" is a payload problem — so they arrive in the
    same envelope, with the same schema and example, as a missing field.
    """
    return ValidationFailedError.build(
        op.name,
        [ValidationIssue(field, message, code)],
        expected_schema=op.pydantic_model.model_json_schema(),
        example=op.example,
    )


def _wrap_backend(op: WriteOperation, exc: Exception, *, path: str) -> BackendError:
    status = getattr(exc, "http_status", None)
    return BackendError.build(op.name, status or 502, str(exc), method="GET", path=path)


# --- resolution (live path only) ------------------------------------------ #


async def _resolve_project_id(op: WriteOperation, client: OpikClient, name: str) -> str:
    """Project name → id, or a validation failure naming what to do instead.

    Deliberately does NOT create the project. opik-backend's dashboard
    endpoint would create one for an unknown ``project_name``, which turns a
    typo into a permanent empty project charting nothing.
    """
    try:
        page = await client.list_projects(name=name, size=10)
    except (OpikAuthError, OpikValidationError, OpikServerError) as exc:
        raise _wrap_backend(op, exc, path="/v1/private/projects") from exc
    candidates = [
        item
        for item in (page.get("content") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    exact = [c for c in candidates if c.get("name") == name]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if len(candidates) == 1:
        return str(candidates[0]["id"])
    if not candidates:
        raise _fail(
            op,
            "project_name",
            f"no project named {name!r} in this workspace. list('project') shows "
            "what is there; pass project_id to be explicit.",
            "project_not_found",
        )
    listed = ", ".join(f"{c.get('name')!r} ({c['id']})" for c in candidates[:10])
    raise _fail(
        op,
        "project_name",
        f"{len(candidates)} projects match {name!r} — pass project_id for the one "
        f"you mean: {listed}",
        "project_ambiguous",
    )


async def _resolve_dashboard(op: WriteOperation, client: OpikClient, ref: str) -> dict[str, Any]:
    """Dashboard id or name → the stored record (including its ``config``).

    Tries the id route first because that is the cheap, unambiguous case,
    then falls back to a name search. A name matching several dashboards is
    refused with the ids listed: editing the wrong dashboard is silent
    damage the caller would not notice.
    """
    try:
        return await client.get_dashboard(ref)
    except (OpikNotFoundError, OpikValidationError):
        pass
    except (OpikAuthError, OpikServerError) as exc:
        raise _wrap_backend(op, exc, path=f"{DASHBOARDS_ENDPOINT}/{ref}") from exc

    try:
        page = await client.list_dashboards(name=ref, size=10)
    except (OpikAuthError, OpikValidationError, OpikServerError) as exc:
        raise _wrap_backend(op, exc, path=DASHBOARDS_ENDPOINT) from exc
    matches = [
        item
        for item in (page.get("content") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    exact = [m for m in matches if m.get("name") == ref]
    pick = exact[0] if len(exact) == 1 else (matches[0] if len(matches) == 1 else None)
    if pick is None:
        if not matches:
            raise _fail(
                op,
                "dashboard",
                f"no dashboard {ref!r} in this workspace. list('dashboard') shows "
                "the dashboards you can edit.",
                "dashboard_not_found",
            )
        listed = ", ".join(f"{m.get('name')!r} ({m['id']})" for m in matches[:10])
        raise _fail(
            op,
            "dashboard",
            f"{len(matches)} dashboards match {ref!r} — pass the id: {listed}",
            "dashboard_ambiguous",
        )
    try:
        return await client.get_dashboard(str(pick["id"]))
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as exc:
        raise _wrap_backend(op, exc, path=f"{DASHBOARDS_ENDPOINT}/{pick['id']}") from exc


async def _resolve_chart_projects(
    op: WriteOperation,
    client: OpikClient,
    charts: list[ChartSpec],
    *,
    default_project_id: str | None,
) -> list[ChartSpec]:
    """Resolve every chart's ``project_name``, and inherit the dashboard's project.

    Inheritance matters more than it looks: a widget with no project scope
    renders as "not configured" in the UI, so a chart added to a
    project-scoped dashboard without one would save fine and display
    nothing. A chart that names its own project keeps it.

    Names are resolved once per distinct name — a dashboard is routinely
    six charts over the same project.
    """
    if not charts:
        return []
    cache: dict[str, str] = {}
    resolved: list[ChartSpec] = []
    for chart in charts:
        if chart.project_name is not None:
            name = chart.project_name
            if name not in cache:
                cache[name] = await _resolve_project_id(op, client, name)
            resolved.append(chart.with_project_id(cache[name]))
        elif (
            default_project_id
            and chart.project_id is None
            and not chart.project_ids
            and not chart.all_projects
        ):
            resolved.append(chart.with_project_id(default_project_id))
        else:
            resolved.append(chart)
    return resolved


def dashboard_url(dashboard_id: str, settings: Settings) -> str | None:
    """Deep link to the dashboard in the Opik UI, when the config allows one.

    Same derivation as the instructions blob: the REST base minus its
    ``/api`` suffix is the UI origin. Without a workspace name there is no
    route to build, so we return ``None`` rather than a link that 404s.
    """
    base = opik_rest_base(settings)
    workspace = settings.comet_workspace
    if base is None or not workspace:
        return None
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return f"{base}/{workspace}/dashboards/{dashboard_id}"


async def resolve_context(
    op: WriteOperation,
    model: Any,
    client: OpikClient,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Everything :func:`build_request` needs that only the backend knows.

    Returns ``{}`` for operations this module does not handle, so the
    dispatcher can call it unconditionally.
    """
    if op.name not in DASHBOARD_OPERATIONS:
        return {}
    s = settings if settings is not None else get_settings()

    if op.name == "dashboard.create":
        project_id: str | None = None
        if model.project_name is not None:
            project_id = await _resolve_project_id(op, client, model.project_name)
        elif model.project_id is not None:
            project_id = str(model.project_id)
        charts = await _resolve_chart_projects(
            op, client, list(model.charts), default_project_id=project_id
        )
        return {"project_id": project_id, "charts": charts, "settings": s}

    record = await _resolve_dashboard(op, client, model.dashboard)
    dashboard_id = str(record.get("id") or model.dashboard)
    context: dict[str, Any] = {
        "dashboard": record,
        "dashboard_id": dashboard_id,
        "dashboard_url": dashboard_url(dashboard_id, s),
        "settings": s,
    }
    # ``update`` and ``add_charts`` carry charts; ``remove_charts`` does not.
    model_charts = getattr(model, "charts", None)
    if model_charts:
        record_project = record.get("project_id")
        context["charts"] = await _resolve_chart_projects(
            op,
            client,
            list(model_charts),
            default_project_id=record_project if isinstance(record_project, str) else None,
        )
    return context


# --- compilation ---------------------------------------------------------- #


def _widget_summary(widget: dict[str, Any]) -> dict[str, Any]:
    return {
        "widget_id": widget.get("id"),
        "title": widget.get("title"),
        "type": widget.get("type"),
    }


def build_request(
    op: WriteOperation,
    model: Any,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """``(path, body)`` for a dashboard operation.

    With an empty ``context`` this is the ``dry_run`` preview: the path
    carries whatever the caller passed for ``dashboard`` (a name is resolved
    at execution) and the config edits show the widgets that WOULD be added
    rather than a merged config this code has not fetched.

    ``context["result"]`` is filled in as a side effect — the ids and the
    deep link the caller needs next, which are only knowable here.
    """
    if op.name == "dashboard.create":
        return _build_create(model, context)
    if op.name == "dashboard.update":
        return _build_update(model, context)
    if op.name == "dashboard.add_charts":
        return _build_add_charts(op, model, context)
    if op.name == "dashboard.remove_charts":
        return _build_remove_charts(op, model, context)
    raise RuntimeError(f"dashboard_ops: unhandled operation {op.name!r}")  # pragma: no cover


def _build_create(model: Any, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    charts: list[ChartSpec] = context.get("charts") or list(model.charts)
    config = build_config(charts, section_title=model.section_title)
    body: dict[str, Any] = {"name": model.name, "type": model.type, "config": config}
    if model.description is not None:
        body["description"] = model.description
    project_id = context.get("project_id") or (
        str(model.project_id) if model.project_id is not None else None
    )
    if project_id is not None:
        body["project_id"] = project_id
    context["result"] = {
        "charts_added": [_widget_summary(w) for w in _config_widgets(config)],
        # The dashboard id is minted by the backend, so the link is built by
        # the dispatcher from the response body rather than here.
    }
    return DASHBOARDS_ENDPOINT, body


def _config_widgets(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [widget for _section, widget in iter_widgets(config)]


def _patch_path(model: Any, context: dict[str, Any]) -> str:
    return f"{DASHBOARDS_ENDPOINT}/{context.get('dashboard_id') or model.dashboard}"


def _result(context: dict[str, Any], **fields: Any) -> None:
    context["result"] = {
        k: v
        for k, v in {
            "dashboard_id": context.get("dashboard_id"),
            "dashboard_url": context.get("dashboard_url"),
            **fields,
        }.items()
        if v is not None
    }


def _build_update(model: Any, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    body: dict[str, Any] = {}
    if model.name is not None:
        body["name"] = model.name
    if model.description is not None:
        body["description"] = model.description
    if model.type is not None:
        body["type"] = model.type
    if model.charts is not None:
        charts: list[ChartSpec] = context.get("charts") or list(model.charts)
        # A wholesale config replacement, and the operation says so: every
        # existing widget, section and layout is dropped for these charts.
        config = build_config(charts, section_title=model.section_title)
        body["config"] = config
        _result(context, charts_replaced=[_widget_summary(w) for w in _config_widgets(config)])
    else:
        _result(context)
    return _patch_path(model, context), body


def _build_add_charts(
    op: WriteOperation, model: Any, context: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    charts: list[ChartSpec] = context.get("charts") or list(model.charts)
    record = context.get("dashboard")
    if record is None:
        # dry_run: no fetch happened. Show the widgets that would be appended;
        # the real body is the whole merged config (see ``preview_note``).
        preview = [chart.to_widget(f"<generated#{i + 1}>") for i, chart in enumerate(charts)]
        return _patch_path(model, context), {"charts_to_add": preview}

    config = record.get("config")
    added: list[dict[str, Any]] = []
    try:
        for chart in charts:
            config, widget = add_chart(config, chart, section=model.section)
            added.append(widget)
    except ValueError as exc:  # DashboardConfigError
        raise _fail(op, "dashboard", str(exc), "dashboard_config_unreadable") from exc
    _result(context, charts_added=[_widget_summary(w) for w in added])
    return _patch_path(model, context), {"config": config}


def _build_remove_charts(
    op: WriteOperation, model: Any, context: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    record = context.get("dashboard")
    if record is None:
        return _patch_path(model, context), {"widget_ids_to_remove": list(model.widget_ids)}

    config = record.get("config")
    removed: list[dict[str, Any]] = []
    try:
        for widget_id in model.widget_ids:
            config, widget = remove_widget(config, widget_id)
            removed.append(widget)
    except ValueError as exc:  # DashboardConfigError — unknown id, or a bad config
        raise _fail(op, "widget_ids", str(exc), "widget_not_found") from exc
    _result(context, charts_removed=[_widget_summary(w) for w in removed])
    return _patch_path(model, context), {"config": config}


def preview_note(op: WriteOperation) -> str | None:
    """What a ``dry_run`` of this operation is NOT showing, or ``None``.

    Only the parts that need the backend: a preview that looks exact when it
    is not is worse than no preview, because the caller stops checking.
    """
    if op.name == "dashboard.create":
        return (
            "project_name values (on the dashboard and on each chart) are resolved "
            "to project ids at execution; widget ids are generated then too."
        )
    if op.name in ("dashboard.add_charts", "dashboard.remove_charts"):
        return (
            "the live call fetches the dashboard's current config and sends the whole "
            "merged config; this preview shows only the change, and `dashboard` is "
            "resolved from a name to an id at execution."
        )
    if op.name == "dashboard.update":
        return "`dashboard` is resolved from a name to an id at execution."
    return None


__all__ = [
    "DASHBOARDS_ENDPOINT",
    "DASHBOARD_OPERATIONS",
    "build_request",
    "dashboard_url",
    "preview_note",
    "resolve_context",
]

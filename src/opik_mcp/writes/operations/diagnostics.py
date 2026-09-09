"""Turning Diagnostics on for a project, scanning it, and retiring an issue.

Five operations over two backend routes, and between them every hook the
dispatcher offers, because Diagnostics needs something at each point:

- before the request, the project has to be a UUID (the job routes take no
  name) and, for the job operations, the deployment has to be able to scan at
  all;
- the request itself puts its subject in the path and little or nothing in
  the body;
- two backend answers mean something other than what they say;
- and afterwards the caller wants the page to open.

All of it lives here so ``dispatch`` does not have to know that Diagnostics
exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikClient
from opik_mcp.read_list.diagnostics import UNAVAILABLE_SENTENCE, diagnostics_available
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.project_scope import resolve_project_id
from opik_mcp.read_list.ui_links import project_page_url
from opik_mcp.writes.errors import BackendError
from opik_mcp.writes.wire import BuildContext, WireRequest, dump, refuse, safe_body

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation

#: The job operations. Both take the project in the path as a UUID and are
#: pointless where the deployment has no Ollie, so they are treated alike
#: rather than branched on by name.
JOB_OPS = frozenset({"agent_insights_job.enable", "agent_insights_job.trigger"})

#: Issue lifecycle moves, and the status each one writes. One backend route
#: with three verbs in front of it: the caller says what they mean and this
#: supplies the value, so nobody has to remember the enum.
ISSUE_STATUS = {
    "agent_insights_issue.resolve": "resolved",
    "agent_insights_issue.close": "closed",
    "agent_insights_issue.reopen": "open",
}
ISSUE_OPS = frozenset(ISSUE_STATUS)

_NOT_ENABLED = (
    "Diagnostics is not enabled for this project (or this backend has no "
    "Diagnostics API); enable it first with write('agent_insights_job.enable', …)."
)


async def prepare_scope(
    op: WriteOperation, items: list[BaseModel], client: OpikClient
) -> str | None:
    """Refuse where Diagnostics cannot run, and resolve the project to the UUID
    the backend takes: in the path for a job, in the body for an issue.

    The gate is for the job operations only. An issue operation acts on an
    issue that exists, which is proof Diagnostics runs here, so asking the
    toggles endpoint would spend a request to learn what the issue already
    establishes.
    """
    if op.name in JOB_OPS and not await diagnostics_available(client):
        raise refuse(op, "", UNAVAILABLE_SENTENCE, "diagnostics_unavailable")
    item = items[0]
    project_id = getattr(item, "project_id", None)
    if project_id is not None:
        return str(project_id)
    project_name = getattr(item, "project_name", None)
    try:
        return await resolve_project_id(client, str(project_name))
    except EntityArgValidationError as e:
        raise refuse(op, "project_name", str(e), "project_scope_missing") from e


def build_job_action(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    """The project is in the path and the payload was only ever scope, so the
    body is empty. A dry run has no resolved id unless the caller passed one,
    and echoes the template in its place."""
    project_id = ctx.prepared or _passed_project_id(items) or "{project_id}"
    return WireRequest(op.endpoint.format(project_id=project_id))


def build_issue_action(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """The issue is in the path; the body is the project and the new status."""
    single = dump(items[0])
    scope = ctx.prepared or _passed_project_id(items) or "{project_id}"
    return WireRequest(
        op.endpoint.format(issue_id=single["issue_id"]),
        {"project_id": scope, "status": ISSUE_STATUS[op.name]},
    )


def _passed_project_id(items: list[BaseModel]) -> str | None:
    """A UUID the caller already gave needs no network, so a dry run can show
    the real path instead of a template."""
    raw = getattr(items[0], "project_id", None)
    return str(raw) if raw is not None else None


async def retry(
    op: WriteOperation,
    client: OpikClient,
    request: WireRequest,
    resp: httpx.Response,
) -> tuple[WireRequest, httpx.Response]:
    """Two backend answers that mean something other than what they say.

    A 409 on enable means the job exists, which is not a failure of "turn it
    on": flip its status instead, so a repeated enable is safe. The follow-up
    is a different method and body, so it gets no idempotency key — replaying
    the create's key could hand back the stored 409 and report it as the
    PATCH's result. If the follow-up fails, the original conflict is carried
    into the error, so a 409 that meant something else is not lost behind a
    PATCH failure.

    A 404 on trigger means the project has no job: a missing prerequisite, not
    a lost resource, so name the fix.
    """
    if op.name == "agent_insights_job.enable" and resp.status_code == 409:
        conflict = safe_body(resp)
        request = WireRequest(request.path, {"status": "enabled"}, method="PATCH")
        resp = await client.write_json(request.method or op.method, request.path, request.body)
        if not (200 <= resp.status_code < 300):
            raise BackendError.build(
                op.name,
                resp.status_code,
                {"create_conflict": conflict, "update_error": safe_body(resp)},
                method=request.method or op.method,
                path=request.path,
            )
    if op.name == "agent_insights_job.trigger" and resp.status_code == 404:
        raise refuse(op, "", _NOT_ENABLED, "diagnostics_not_enabled")
    return request, resp


def decorate(
    op: WriteOperation,
    items: list[BaseModel],
    out: dict[str, Any],
    settings: Settings,
    project_id: str | None,
) -> None:
    """Add the page to open and, for a trigger, what to expect there."""
    if project_id is None:
        return
    if op.name in ISSUE_OPS:
        # A resolved or closed issue leaves the default page, so a link to
        # "diagnostics" would land on a page the issue is no longer on.
        view = "diagnostics" if ISSUE_STATUS[op.name] == "open" else "diagnostics/resolved"
        issue_id = getattr(items[0], "issue_id", None)
        page = project_page_url(settings, project_id, f"{view}?issue={issue_id}")
        if page is not None:
            out["url"] = page
        return
    page = project_page_url(settings, project_id, "diagnostics")
    if page is not None:
        out["url"] = page
    if op.name == "agent_insights_job.trigger":
        out["note"] = (
            "Scan started over the last 24 hours. It takes a few minutes; "
            "read the issues afterwards with list('agent_insights_issue', …), "
            "and watch the run on the Diagnostics page."
        )


def dry_run_note(op: WriteOperation, items: list[BaseModel], prepared: str | None) -> str | None:
    """What a preview cannot show, and only when the caller passed a name:
    with a UUID in hand the preview is the real request, and saying otherwise
    would undersell it."""
    if _passed_project_id(items) is not None:
        return None
    if op.name in JOB_OPS:
        return (
            "project_name is resolved to the project's UUID at execution; the live path "
            "will carry that id. The live call also refuses if the deployment has no "
            "Diagnostics, which dry_run does not check."
        )
    return (
        "project_name is resolved to the project's UUID at execution; the live body "
        "will carry that id."
    )


__all__ = [
    "ISSUE_OPS",
    "ISSUE_STATUS",
    "JOB_OPS",
    "build_issue_action",
    "build_job_action",
    "decorate",
    "dry_run_note",
    "prepare_scope",
    "retry",
]

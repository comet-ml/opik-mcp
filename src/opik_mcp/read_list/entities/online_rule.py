"""``online_rule`` — the automation rule evaluators scoring a project.

Where most of a project's score names come from, which is why the overview
names them and this enumerates them with their kind, whether they are on, and
their sampling rate.
"""

from __future__ import annotations

from typing import Unpack

from opik_mcp.client.protocols import OpikListClient
from opik_mcp.client.shapes import AutomationRule, Page
from opik_mcp.read_list.handler import EntityHandler, ScopedPageKwargs, page_kwargs
from opik_mcp.read_list.project_scope import scope_of
from opik_mcp.read_list.ui_links import ViewPage
from opik_mcp.read_list.unsupported import unsupported_fetch


async def list_page(client: OpikListClient, **kw: Unpack[ScopedPageKwargs]) -> Page[AutomationRule]:
    project_id = await scope_of(client, kw, caller="list('online_rule')")
    return await client.list_automation_rules(project_id=project_id, **page_kwargs(kw))


HANDLER = EntityHandler(
    entity_type="online_rule",
    view_page=ViewPage(
        area="online-evaluation",
        opens="the project's Online evaluation rules, where this rule is a row",
        opens_empty="the project's Online evaluation, where a rule can be created",
    ),
    fetch_fn=unsupported_fetch,
    list_fn=list_page,
    list_extra_fields=("type", "enabled", "sampling_rate"),
    list_required_kwargs=("project_id",),
    description=(
        "An automation rule evaluator on a project — what scores the traces as "
        "they arrive, and so where most of its score names come from."
    ),
)

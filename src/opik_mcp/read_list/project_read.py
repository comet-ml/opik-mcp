"""``read('project')`` — the record, plus what the question behind it needs.

The registry is a table of entities; this is one entity's read, and it is the
only one that fans out. Keeping it here leaves the registry entry a single
line, the way ``project_metric`` already delegates its whole runner, and keeps
the table from doubling as an orchestrator.

What the fan-out is for: the record alone carries no numbers — ``GET
/projects/{id}`` serves the ``View.Public`` projection and every aggregate
lives on ``View.Detailed`` — so "how is my project doing" needed a second call
whatever we did. Answering it here means the agent gets figures on the first
call instead of a name and a creation date, and gets the project's own
vocabulary with them, so the next question can be asked in the project's
terms rather than in guesses.
"""

from __future__ import annotations

import asyncio
from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list import project_vocabulary
from opik_mcp.read_list.project_contents import project_contents
from opik_mcp.read_list.project_summary import trace_summary
from opik_mcp.read_list.ui_links import project_page_url


async def fetch_project(
    client: OpikReadClient,
    entity_id: str,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Project record + the figures, the vocabulary and the freshest work."""
    # The record first, on its own: it is the primary payload, and a bad id or
    # a project in another workspace should cost one call rather than fanning
    # out four against something we cannot read. Everything after it is
    # decoration, so it goes out together — on the one connection this call
    # owns, that is a single round trip instead of four.
    project = await client.get_project(entity_id)
    # Every leg is wrapped in `decorations.block`, so a failure inside one
    # comes back as data. `return_exceptions` is still set: if a leg ever
    # raises something the block does not catch, the siblings must be
    # collected rather than left running against a connection this call is
    # about to close.
    legs = await asyncio.gather(
        trace_summary(client, entity_id, since=since, until=until),
        project_vocabulary.score_names(client, entity_id),
        project_vocabulary.usage_keys(client, entity_id),
        project_vocabulary.online_rules(client, entity_id),
        project_contents(client, entity_id),
        return_exceptions=True,
    )
    for leg in legs:
        if isinstance(leg, BaseException):
            raise leg
    summary, scores, usage, rules, contents = legs

    data: dict[str, Any] = {
        "project": project,
        "summary": summary,
        # link_fn needs the project the read was addressed by; underscore keys
        # are stripped by the read tool once links are attached.
        "_project_id": entity_id,
    }
    vocabulary = project_vocabulary.assemble(scores, usage, rules)
    if vocabulary is not None:
        data["vocabulary"] = vocabulary
    if contents is not None:
        data["contains"] = contents
    return data


def project_links(settings: Settings, data: dict[str, Any]) -> dict[str, str]:
    """The project's Logs page — where the summary's numbers are on screen."""
    project_id = data.get("_project_id")
    if not isinstance(project_id, str):
        return {}
    page = project_page_url(settings, project_id, "logs")
    return {} if page is None else {"url": page}


__all__ = ["fetch_project", "project_links"]

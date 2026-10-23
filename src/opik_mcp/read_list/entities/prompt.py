"""``prompt`` and its versions — the template, and its history.

The read inlines the versions: "what does this prompt say" is answered by the
latest one and "what changed" by the list, and both in one call cost less than
a read plus a list of a collection this small.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Unpack

from opik_mcp.client.protocols import OpikListClient, OpikReadClient
from opik_mcp.client.shapes import Page, Prompt, PromptVersion
from opik_mcp.config import Settings
from opik_mcp.read_list.handler import (
    EntityHandler,
    NamedPageKwargs,
    PageKwargs,
    ParentPage,
    page_kwargs,
)
from opik_mcp.read_list.paging import (
    NameCandidate,
    collection_total,
    collection_truncated,
    continuation,
    name_candidates,
    page_items,
    rest_of,
)
from opik_mcp.read_list.ui_links import scoped_entity_links
from opik_mcp.read_list.unsupported import unsupported_fetch
from opik_mcp.read_list.uri import opik_uri

VERSIONS_INLINE_LIMIT = 100


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, object]:
    """Prompt + full version list (up to ``VERSIONS_INLINE_LIMIT``)."""
    prompt = await client.get_prompt(entity_id)
    try:
        versions_page = await client.list_prompt_versions(
            entity_id, page=1, size=VERSIONS_INLINE_LIMIT
        )
    except Exception:
        return {"prompt": prompt, "versions": [], "versionsTruncated": False}
    versions = page_items(versions_page)
    truncated = collection_truncated(
        versions_page, inlined=len(versions), limit=VERSIONS_INLINE_LIMIT
    )
    result: dict[str, object] = {
        "prompt": prompt,
        "versions": versions,
        "versionsTruncated": truncated,
    }
    if truncated:
        result["moreVersions"] = rest_of(
            "versions",
            inlined=len(versions),
            total=collection_total(versions_page),
            call=(
                f"list('prompt_version', prompt_id='{entity_id}', "
                f"{continuation(VERSIONS_INLINE_LIMIT)})"
            ),
        )
    return result


async def search_by_name(client: OpikReadClient, name: str) -> list[NameCandidate]:
    return name_candidates(await client.list_prompts(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Unpack[NamedPageKwargs]) -> Page[Prompt]:
    return await client.list_prompts(**kw)


class ListVersionsKwargs(PageKwargs, total=False):
    prompt_id: str


async def list_versions(
    client: OpikListClient, **kw: Unpack[ListVersionsKwargs]
) -> Page[PromptVersion]:
    prompt_id = kw.get("prompt_id")
    if not prompt_id:
        raise ValueError("list prompt_version requires prompt_id")
    return await client.list_prompt_versions(prompt_id, **page_kwargs(kw))


async def fetch_prompt_record(client: OpikReadClient, prompt_id: str) -> Prompt:
    return await client.get_prompt(prompt_id)


def prompt_links(settings: Settings, data: Mapping[str, object]) -> dict[str, str]:
    """The prompt's page under its project, or why there is none.

    Same shape as a dataset's: ``project_id`` is nullable on the backend, and
    a prompt created without project scope has no page in v2 at all.
    """
    prompt = data.get("prompt")
    record = prompt if isinstance(prompt, dict) else data
    return scoped_entity_links(settings, record, area="prompts", noun="prompt")


HANDLER = EntityHandler(
    entity_type="prompt",
    is_name_searchable=True,
    uri_patterns=(opik_uri("prompts/{id}"),),
    link_fn=prompt_links,
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    list_extra_fields=("version_count", "created_at"),
    description=(
        "Prompt metadata + full version list (up to 100 inlined). Returns {prompt, "
        "versions, versionsTruncated}, plus moreVersions with the call for the rest "
        "past 100."
    ),
)


VERSION_HANDLER = EntityHandler(
    entity_type="prompt_version",
    fetch_fn=unsupported_fetch,
    parent_page=ParentPage(fetch=fetch_prompt_record, area="prompts", subpath="", noun="prompt"),
    list_fn=list_versions,
    list_extra_fields=("template", "created_at"),
    list_required_kwargs=("prompt_id",),
    id_only=True,
    description=(
        "Prompt version. Currently list-only — pass prompt_id to enumerate. "
        "Use read('prompt', id) to get the prompt + all versions in one call."
    ),
)

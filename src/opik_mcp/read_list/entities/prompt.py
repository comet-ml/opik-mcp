"""``prompt`` and its versions — the template, and its history.

The read inlines the versions: "what does this prompt say" is answered by the
latest one and "what changed" by the list, and both in one call cost less than
a read plus a list of a collection this small.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import collection_truncated, name_candidates, page_items
from opik_mcp.read_list.unsupported import unsupported_fetch

VERSIONS_INLINE_LIMIT = 100


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
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
    return {"prompt": prompt, "versions": versions, "versionsTruncated": truncated}


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_prompts(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_prompts(**kw)


async def list_versions(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    prompt_id = kw.pop("prompt_id", None)
    if not prompt_id:
        raise ValueError("list prompt_version requires prompt_id")
    kw.pop("name", None)
    return await client.list_prompt_versions(prompt_id, **kw)


HANDLER = EntityHandler(
    entity_type="prompt",
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    list_extra_fields=("version_count", "created_at"),
    description=(
        "Prompt metadata + full version list. Returns {prompt, versions, versionsTruncated}."
    ),
)


VERSION_HANDLER = EntityHandler(
    entity_type="prompt_version",
    fetch_fn=unsupported_fetch,
    list_fn=list_versions,
    list_extra_fields=("template", "created_at"),
    list_required_kwargs=("prompt_id",),
    id_only=True,
    description=(
        "Prompt version. Currently list-only — pass prompt_id to enumerate. "
        "Use read('prompt', id) to get the prompt + all versions in one call."
    ),
)

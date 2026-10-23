"""Prompts and their version history."""

from __future__ import annotations

from typing import cast

from opik_mcp.client.base import OpikClientBase, QueryParams
from opik_mcp.client.shapes import Page, Prompt, PromptVersion


class PromptEndpoints(OpikClientBase):
    async def list_prompts(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> Page[Prompt]:
        """``GET /v1/private/prompts`` — Spring Page envelope."""
        params: QueryParams = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        return cast(
            Page[Prompt],
            await self._get_json(
                "/v1/private/prompts",
                params=params,
                entity_hint="prompts",
            ),
        )

    async def get_prompt(self, prompt_id: str) -> Prompt:
        """``GET /v1/private/prompts/{id}`` — singleton prompt record.

        opik-backend MAY include ``latestVersion`` inline but does not
        guarantee it (verified live on dev.comet.com: some prompts return
        without the field). Callers needing the full version history use
        ``list_prompt_versions`` — that's the single source of truth.
        """
        return cast(
            Prompt,
            await self._get_json(
                f"/v1/private/prompts/{prompt_id}",
                params=None,
                entity_hint=f"prompt {prompt_id!r}",
            ),
        )

    async def list_prompt_versions(
        self,
        prompt_id: str,
        *,
        page: int = 1,
        size: int = 10,
    ) -> Page[PromptVersion]:
        """``GET /v1/private/prompts/{id}/versions`` — full version history."""
        return cast(
            Page[PromptVersion],
            await self._get_json(
                f"/v1/private/prompts/{prompt_id}/versions",
                params={"page": page, "size": size},
                entity_hint=f"prompt {prompt_id!r} versions",
            ),
        )

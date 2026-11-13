"""Automations: the automations themselves, their runs, the recipe states and
the recommendations under ``/v1/private/automations``.

The read routes are here. The write routes (create, update, delete, test,
workflow enable and disable, dismiss) go through ``write_json`` from
``writes/operations/automations.py``, like every other write.
"""

from __future__ import annotations

from opik_mcp.client.base import OpikClientBase, _drop_none

_PREFIX = "/v1/private/automations"


class AutomationEndpoints(OpikClientBase):
    async def list_automations(
        self,
        *,
        project_id: str,
        enabled: str | None = None,
        recipe: str | None = None,
        event: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, object]:
        """``GET /v1/private/automations`` — a project's automations, with 7-day stats.

        ``enabled`` travels as the lowercase literal the backend's boolean
        query parser expects, so it is a string here and not a bool.
        """
        return await self._get_json(
            _PREFIX,
            params=_drop_none(
                {
                    "project_id": project_id,
                    "enabled": enabled,
                    "recipe": recipe,
                    "event": event,
                    "page": page,
                    "size": size,
                }
            ),
            entity_hint="automations",
        )

    async def get_automation(self, automation_id: str, /) -> dict[str, object]:
        """``GET /v1/private/automations/{id}`` — one automation, with its ``stats``
        and ``paused_reason``."""
        return await self._get_json(
            f"{_PREFIX}/{automation_id}",
            params=None,
            entity_hint=f"automation {automation_id!r}",
        )

    async def list_automation_runs(
        self,
        *,
        project_id: str | None = None,
        automation_id: str | None = None,
        status: str | None = None,
        since: str | None = None,
        parent_run_id: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, object]:
        """``GET /v1/private/automations/runs`` — runs by project or by automation."""
        return await self._get_json(
            f"{_PREFIX}/runs",
            params=_drop_none(
                {
                    "project_id": project_id,
                    "automation_id": automation_id,
                    "status": status,
                    "since": since,
                    "parent_run_id": parent_run_id,
                    "page": page,
                    "size": size,
                }
            ),
            entity_hint="automation runs",
        )

    async def get_automation_run(self, run_id: str, /) -> dict[str, object]:
        """``GET /v1/private/automations/runs/{run_id}`` — one run with its parent and children."""
        return await self._get_json(
            f"{_PREFIX}/runs/{run_id}",
            params=None,
            entity_hint=f"automation run {run_id!r}",
        )

    async def list_workflows(self, *, project_id: str) -> list[object]:
        """``GET /v1/private/automations/workflows`` — each recipe's state for a project."""
        return await self._get_json_list(
            f"{_PREFIX}/workflows",
            params={"project_id": project_id},
            entity_hint="workflows",
        )

    async def list_recommendations(self, *, project_id: str) -> list[object]:
        """``GET /v1/private/automations/recommendations`` — ranked next steps, never empty."""
        return await self._get_json_list(
            f"{_PREFIX}/recommendations",
            params={"project_id": project_id},
            entity_hint="recommendations",
        )

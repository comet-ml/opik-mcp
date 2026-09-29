"""Diagnostics (Agent Insights) issues and jobs, and the deployment's service toggles."""

from __future__ import annotations

from typing import cast

from opik_mcp.client.base import OpikClientBase, _drop_none
from opik_mcp.client.json_value import JsonObject
from opik_mcp.client.shapes import (
    AgentInsightsIssue,
    AgentInsightsIssueWithDetails,
    AgentInsightsJob,
    Page,
)


class DiagnosticsEndpoints(OpikClientBase):
    async def list_agent_insights_issues(
        self,
        *,
        project_id: str,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> Page[AgentInsightsIssue]:
        """``GET /v1/private/agent-insights/issues`` — a project's Diagnostics issues.

        The backend takes ``project_id`` only (no ``project_name``), so name
        resolution is the caller's job. ``status`` filters open/resolved/closed;
        ``from_date`` / ``to_date`` (ISO dates) bound the aggregation window.
        Omitted filters are not sent, which the backend reads as all statuses
        and all-time — exactly what the Diagnostics page shows.
        """
        return cast(
            Page[AgentInsightsIssue],
            await self._get_json(
                "/v1/private/agent-insights/issues",
                params=_drop_none(
                    {
                        "project_id": project_id,
                        "status": status,
                        "from_date": from_date,
                        "to_date": to_date,
                        "page": page,
                        "size": size,
                    }
                ),
                entity_hint="agent insights issues",
            ),
        )

    async def get_agent_insights_job(self, project_id: str) -> AgentInsightsJob:
        """``GET /v1/private/agent-insights/jobs/{projectId}`` — the project's
        Diagnostics job: ``status`` (enabled/disabled), ``last_scan_at`` and the
        last failure fields. 404 means Diagnostics was never enabled for the
        project, which callers treat as a state, not an error.
        """
        return cast(
            AgentInsightsJob,
            await self._get_json(
                f"/v1/private/agent-insights/jobs/{project_id}",
                params=None,
                entity_hint=f"agent insights job for project {project_id!r}",
            ),
        )

    async def get_service_toggles(self) -> JsonObject:
        """``GET /v1/private/toggles/`` — the deployment's service toggles.

        The same endpoint the UI's feature-toggle provider reads, so the MCP
        and the UI can never disagree about which features a deployment has.
        """
        return await self._get_json(
            "/v1/private/toggles/",
            params=None,
            entity_hint="service toggles",
        )

    async def get_agent_insights_issue(
        self,
        issue_id: str,
        *,
        project_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> AgentInsightsIssueWithDetails:
        """``GET /v1/private/agent-insights/issues/{id}`` — one issue + per-day details.

        An issue id is unique, but the backend still requires ``project_id`` as
        a query parameter (it scopes the tenancy check). ``details`` is one row
        per report day inside the window, ascending; each row's free-form
        ``metadata`` is where the Diagnostics job puts ``example_trace_ids``.
        """
        return cast(
            AgentInsightsIssueWithDetails,
            await self._get_json(
                f"/v1/private/agent-insights/issues/{issue_id}",
                params=_drop_none(
                    {"project_id": project_id, "from_date": from_date, "to_date": to_date}
                ),
                entity_hint=f"agent insights issue {issue_id!r}",
            ),
        )

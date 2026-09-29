"""Project records, KPI cards, metrics, score and usage names, activity and online rules."""

from __future__ import annotations

import json as _json
from typing import Any

from opik_mcp.client.base import OpikClientBase, _drop_none


class ProjectEndpoints(OpikClientBase):
    async def list_projects(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
        sorting: str | None = None,
    ) -> dict[str, Any]:
        """``GET /v1/private/projects`` — Spring Page envelope ``{content,page,size,total}``.

        ``name`` is a substring filter (case-insensitive on opik-backend) used
        for the read tool's name-lookup path. ``sorting`` is the same JSON
        array every other listable endpoint takes.
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        if sorting is not None:
            params["sorting"] = sorting
        return await self._get_json(
            "/v1/private/projects",
            params=params,
            entity_hint="projects",
        )

    async def get_project(self, project_id: str) -> dict[str, Any]:
        """``GET /v1/private/projects/{id}`` — single project record.

        Serves the ``View.Public`` projection: metadata and
        ``last_updated_trace_at``, but none of the aggregates
        (``trace_count``, ``error_count``, cost, duration) — those live on
        ``View.Detailed``, which only ``GET /projects/stats`` returns.
        """
        return await self._get_json(
            f"/v1/private/projects/{project_id}",
            params=None,
            entity_hint=f"project {project_id!r}",
        )

    async def get_project_kpi_cards(
        self,
        project_id: str,
        /,
        *,
        entity_type: str,
        interval_start: str,
        interval_end: str | None = None,
        filters: str | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/private/projects/{id}/kpi-cards`` — the Logs page's four cards.

        Returns ``{stats: [{type, current_value, previous_value}]}`` for
        ``count``, ``errors`` (a percentage in [0, 100]), ``avg_duration`` (ms)
        and ``total_cost`` (USD). The previous period is the backend's own:
        ``[start - (end - start), start)``, so a 7-day window compares against
        the 7 days before it.

        Two wire details worth naming, both verified live rather than inferred:
        ``filters`` is a JSON-encoded **string** (``KpiCardRequest`` declares
        ``String filters``), and the entity kind decides the shape — ``threads``
        comes back with three entries, no ``errors`` at all, because the backend
        does not compute an error rate for threads.
        """
        body = _drop_none(
            {
                "entity_type": entity_type,
                "interval_start": interval_start,
                "interval_end": interval_end,
                "filters": filters,
            }
        )
        return await self._post_json(
            f"/v1/private/projects/{project_id}/kpi-cards",
            json=body,
            entity_hint=f"project {project_id!r} KPI cards",
        )

    async def get_project_metrics(
        self,
        project_id: str,
        /,
        *,
        metric_type: str,
        interval: str,
        interval_start: str,
        interval_end: str | None = None,
        trace_filters: list[dict[str, str]] | None = None,
        span_filters: list[dict[str, str]] | None = None,
        thread_filters: list[dict[str, str]] | None = None,
        breakdown: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/private/projects/{id}/metrics`` — one metric over time.

        Returns ``{project_id, metric_type, interval, results: [{name, data:
        [{time, value}]}]}``. One entry in ``results`` per series: one for a
        plain metric, one per group when a breakdown is asked for, and one per
        score name or usage key for the feedback-score and token-usage metrics
        — which is why the width of the answer is not always knowable up front.

        Every bucket in the window is emitted, including the empty ones, so the
        row count follows from ``interval`` and the window alone: 8 for a daily
        week, 169 for an hourly one, 1 for ``TOTAL``.

        Filters are a real array here, unlike ``kpi-cards``, and there is one
        array per entity kind; the caller sends the one its metric belongs to.
        """
        body = _drop_none(
            {
                "metric_type": metric_type,
                "interval": interval,
                "interval_start": interval_start,
                "interval_end": interval_end,
                "trace_filters": trace_filters,
                "span_filters": span_filters,
                "thread_filters": thread_filters,
                "breakdown": breakdown,
            }
        )
        return await self._post_json(
            f"/v1/private/projects/{project_id}/metrics",
            json=body,
            entity_hint=f"project {project_id!r} metrics",
        )

    async def list_project_score_names(self, project_id: str, /) -> dict[str, Any]:
        """``GET /v1/private/projects/feedback-scores/names`` — one project's score names.

        Returns ``{scores: [{name}]}``. The query is the multi-project one
        narrowed to one project, so the id travels as a JSON array.

        Two things this endpoint does *not* do, both load-bearing for callers:
        it does not filter by entity kind (the underlying query has no
        ``entity_type`` predicate, so trace, span and thread names come back
        together), and it does not report a score's ``type`` — the service
        builds each entry from the name alone.
        """
        return await self._get_json(
            "/v1/private/projects/feedback-scores/names",
            params={"project_ids": _json.dumps([project_id], separators=(",", ":"))},
            entity_hint=f"project {project_id!r} score names",
        )

    async def list_project_token_usage_names(self, project_id: str, /) -> dict[str, Any]:
        """``GET /v1/private/projects/{id}/token-usage/names`` — ``{names: [...]}``.

        The usage keys actually recorded in this project — ``prompt_tokens``,
        ``completion_tokens``, whatever else the instrumentation reported. Empty
        for a project whose traces carry no usage.
        """
        return await self._get_json(
            f"/v1/private/projects/{project_id}/token-usage/names",
            params=None,
            entity_hint=f"project {project_id!r} token usage names",
        )

    async def list_project_activities(
        self,
        project_id: str,
        /,
        *,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/projects/{id}/activities`` — recent activity, all kinds.

        One feed across experiments, dataset and test-suite versions, prompt
        versions, optimization runs, alert events and a per-day trace roll-up,
        newest first. ``size`` is capped at 100 by the backend.

        Two shape notes: the owning resource and the author are omitted rather
        than null, and the per-day trace entry carries the day's trace *count*
        in the field every other kind uses for a name.
        """
        return await self._get_json(
            f"/v1/private/projects/{project_id}/activities",
            params={"page": page, "size": size},
            entity_hint=f"project {project_id!r} activity",
        )

    async def list_automation_rules(
        self,
        *,
        project_id: str,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/automations/evaluators/`` — a project's online rules.

        The trailing slash is part of the mapped path; without it the backend
        answers 404.
        """
        return await self._get_json(
            "/v1/private/automations/evaluators/",
            params={"project_id": project_id, "page": page, "size": size},
            entity_hint=f"project {project_id!r} automation rules",
        )

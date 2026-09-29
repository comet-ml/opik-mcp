"""Experiments, their pooled stats and the workspace's feedback definitions."""

from __future__ import annotations

from typing import cast

from opik_mcp.client.base import OpikClientBase, QueryParams, _ids_param, _search_params
from opik_mcp.client.shapes import Experiment, FeedbackDefinition, Page, Stats


class ExperimentEndpoints(OpikClientBase):
    async def list_experiments(
        self,
        *,
        name: str | None = None,
        filters: str | None = None,
        types: str | None = None,
        optimization_id: str | None = None,
        experiment_ids: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 10,
    ) -> Page[Experiment]:
        """``GET /v1/private/experiments`` — Spring Page envelope.

        ``name`` is the backend's case-insensitive partial match. ``types`` is
        a JSON array of ``ExperimentType`` values, already encoded: the
        resource parses it with its own reader and 400s on anything else, so
        it travels as one string rather than as a repeated parameter. The
        search params are accepted for signature parity with the other
        searchable lists and forwarded only when set; the ``list`` tool never
        sends a time window or free-text search for experiments (the backend
        has neither).
        """
        params: QueryParams = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        if types is not None:
            params["types"] = types
        if optimization_id is not None:
            params["optimization_id"] = optimization_id
        if experiment_ids is not None:
            # A JSON array like ``types``; the resource parses it with its own
            # reader and 400s on anything that is not a list of UUIDs.
            params["experiment_ids"] = experiment_ids
        params.update(
            _search_params(
                filters=filters,
                sorting=sorting,
                search=search,
                from_time=from_time,
                to_time=to_time,
                should_truncate=should_truncate,
            )
        )
        return cast(
            Page[Experiment],
            await self._get_json(
                "/v1/private/experiments",
                params=params,
                entity_hint="experiments",
            ),
        )

    async def get_experiment(self, experiment_id: str) -> Experiment:
        """``GET /v1/private/experiments/{id}``."""
        return cast(
            Experiment,
            await self._get_json(
                f"/v1/private/experiments/{experiment_id}",
                params=None,
                entity_hint=f"experiment {experiment_id!r}",
            ),
        )

    async def get_compared_stats(
        self, dataset_id: str, /, *, experiment_ids: list[str], filters: str | None = None
    ) -> Stats:
        """``GET /v1/private/datasets/{id}/items/experiments/items/stats``.

        Count, averages and percentiles over the experiment items of the
        named experiments, pooled — so one experiment per call is what gives
        per-experiment figures. ``filters`` is the same array the joined
        list takes, evaluated on the same rows.
        """
        params: QueryParams = {"experiment_ids": _ids_param(experiment_ids)}
        if filters:
            params["filters"] = filters
        return cast(
            Stats,
            await self._get_json(
                f"/v1/private/datasets/{dataset_id}/items/experiments/items/stats",
                params=params,
                entity_hint=f"dataset {dataset_id!r} experiment stats",
            ),
        )

    async def list_feedback_definitions(
        self, *, page: int = 1, size: int = 10
    ) -> Page[FeedbackDefinition]:
        """``GET /v1/private/feedback-definitions`` — the workspace's score definitions.

        A definition carries the score's type (``numerical``, ``categorical``,
        ``boolean``) and, for a categorical one, the category labels and the
        number each is stored as. It carries no direction.
        """
        return cast(
            Page[FeedbackDefinition],
            await self._get_json(
                "/v1/private/feedback-definitions",
                params={"page": page, "size": size},
                entity_hint="feedback definitions",
            ),
        )

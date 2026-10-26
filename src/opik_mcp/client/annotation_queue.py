"""Annotation queues and the custom annotator view a queue carries."""

from __future__ import annotations

from opik_mcp.client.base import OpikClientBase, _drop_none


class AnnotationQueueEndpoints(OpikClientBase):
    async def list_annotation_queues(
        self,
        *,
        page: int = 1,
        size: int = 10,
        name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
    ) -> dict[str, object]:
        """``GET /v1/private/annotation-queues``: the endpoint has no project param,
        so a project travels as a ``project_id`` clause in ``filters``."""
        params: dict[str, object] = {"page": page, "size": size}
        params.update(_drop_none({"name": name, "filters": filters, "sorting": sorting}))
        return await self._get_json(
            "/v1/private/annotation-queues",
            params=params,
            entity_hint="annotation queues",
        )

    async def get_annotation_queue(self, queue_id: str) -> dict[str, object]:
        """``GET /v1/private/annotation-queues/{id}``: the only call that returns
        ``annotator_view``; list rows never carry it."""
        return await self._get_json(
            f"/v1/private/annotation-queues/{queue_id}",
            params=None,
            entity_hint=f"annotation queue {queue_id!r}",
        )

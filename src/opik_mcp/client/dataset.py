"""Datasets, their items, and items compared across experiments."""

from __future__ import annotations

from typing import Any

from opik_mcp.client.base import OpikClientBase, _ids_param, _search_params


class DatasetEndpoints(OpikClientBase):
    async def list_datasets(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/datasets`` — Spring Page envelope.

        ``name`` is a substring filter used for name-lookup in the read tool.
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if name is not None:
            params["name"] = name
        return await self._get_json(
            "/v1/private/datasets",
            params=params,
            entity_hint="datasets",
        )

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """``GET /v1/private/datasets/{id}`` — one dataset record."""
        return await self._get_json(
            f"/v1/private/datasets/{dataset_id}",
            params=None,
            entity_hint=f"dataset {dataset_id!r}",
        )

    async def list_dataset_items(
        self,
        dataset_id: str,
        *,
        filters: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/datasets/{id}/items`` — paginated item list.

        ``filters`` is the compiled ``DatasetItemFilter`` array, JSON-encoded:
        the cases' own keys (``data`` with the key beside it), the whole
        payload, the id, tags, source and the trace or span each case came
        from. The endpoint has no ``search`` and no ``sorting`` to pass.
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if filters is not None:
            params["filters"] = filters
        return await self._get_json(
            f"/v1/private/datasets/{dataset_id}/items",
            params=params,
            entity_hint=f"dataset {dataset_id!r} items",
        )

    async def get_dataset_item(self, item_id: str) -> dict[str, Any]:
        """``GET /v1/private/datasets/items/{itemId}`` — one case, whole.

        Addressed under ``/datasets/items``, not under the dataset: the id is
        unique on its own, so a caller holding one from a listing needs
        nothing else to read the values the page cut.
        """
        return await self._get_json(
            f"/v1/private/datasets/items/{item_id}",
            params=None,
            entity_hint=f"dataset item {item_id!r}",
        )

    async def list_compared_dataset_items(
        self,
        dataset_id: str,
        /,
        *,
        experiment_ids: list[str],
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """``GET /v1/private/datasets/{id}/items/experiments/items``.

        One row per case with every named experiment's run attached. Filters,
        sorting and search are evaluated on the joined row.
        """
        params: dict[str, Any] = {
            "page": page,
            "size": size,
            "experiment_ids": _ids_param(experiment_ids),
        }
        params.update(
            _search_params(
                filters=filters,
                sorting=sorting,
                search=search,
                from_time=None,
                to_time=None,
                # Bodies never reach the table; let the backend trim them.
                should_truncate=True,
            )
        )
        return await self._get_json(
            f"/v1/private/datasets/{dataset_id}/items/experiments/items",
            params=params,
            entity_hint=f"dataset {dataset_id!r} items compared",
        )

    async def list_compared_output_columns(
        self,
        dataset_id: str,
        /,
        *,
        experiment_ids: list[str],
    ) -> dict[str, Any]:
        """``GET /v1/private/datasets/{id}/items/experiments/items/output/columns``.

        The keys the runs' outputs carry, which is what ``output.<key>``
        filters can name. The dataset's own ``data`` keys come off the page.
        """
        return await self._get_json(
            f"/v1/private/datasets/{dataset_id}/items/experiments/items/output/columns",
            params={"experiment_ids": _ids_param(experiment_ids)},
            entity_hint=f"dataset {dataset_id!r} output columns",
        )

"""The two field tables a dataset item is listed against.

``dataset_item`` is the comparison's joined page, ``dataset_item_case`` the
dataset's own items endpoint. The runner uses the first, the collection path
the second.
"""

from __future__ import annotations

from opik_mcp.read_list.handler import Vocabulary

COMPARED = Vocabulary(
    name="dataset_item",
    sort_fields=(
        "id",
        "created_at",
        "last_updated_at",
        "duration",
        "total_estimated_cost",
        "comments",
        "usage.*",
        "feedback_scores.*",
        "data.*",
        "output.*",
        "input.*",
        "metadata.*",
    ),
)
"""Sorts transcribed from ``SortingFactoryDatasets``, which serves the joined
comparison page.

There is no ``status`` and no pass/fail field on it, so "show me the failed
cases first" cannot be a sort — it is a filter on the score the judge wrote.
Naming that here is the point: the backend logs an unsupported sort field and
answers 200 with an unsorted page, so anything missing from this list has to
be refused before the call.
"""

CASES = Vocabulary(
    name="dataset_item_case",
    unsortable_why=(
        "opik-backend's dataset items endpoint takes no sorting parameter. Only the "
        "comparison orders cases, so a sort needs experiment_ids: "
        "list('dataset_item', experiment_ids=['<uuid>', '<uuid>'], sort='duration desc')"
    ),
)
"""The dataset's own items endpoint orders by nothing at all.

``GET /datasets/{id}/items`` takes ``page``, ``size``, ``version``, ``filters``
and ``truncate`` — there is no ``sorting`` parameter to send, so every sort is
refused here rather than dropped silently. Declared with no sort fields rather
than left out, because a missing entry would make the entity unsortable *and*
make ``schema("list.dataset_item_case")`` raise on its way to saying so.
"""

__all__ = ["CASES", "COMPARED"]

"""The two field tables a dataset item is listed against.

``dataset_item`` is the comparison's joined page, ``dataset_item_case`` the
dataset's own items endpoint. The runner uses the first, the collection path
the second.
"""

from __future__ import annotations

from opik_mcp.read_list.handler import Vocabulary

COMPARED = Vocabulary(
    name="dataset_item",
    filter_examples=(
        "feedback_scores.correctness < 0.5",
        'data.question contains "refund" AND output contains "sorry"',
    ),
    # Every one of these filter fields reads the runs, which exist only when
    # the call names the experiments to compare.
    filter_requirement="experiment_ids: these filters, the sort and search apply to the runs",
    vocabulary_pointer=(
        "without experiment_ids the same list is the dataset's own cases, filtered on the "
        "case itself ({dataset_item_case}) — operators in "
        'schema("list.dataset_item_case")'
    ),
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
    filter_examples=(
        'data.question contains "install"',
        'trace_id = "<trace-uuid>"',
    ),
    vocabulary_pointer=(
        "with experiment_ids the same list is those experiments' runs case by case, filtered "
        'on the runs ({dataset_item}) — operators in schema("list.dataset_item")'
    ),
    field_notes={
        "full_data": (
            "the whole payload as one string, matched case-insensitively: a full scan of the "
            "dataset, with no index behind it. It is the free-text search this endpoint does "
            "not have; a key you can name (data.<key>) is the cheaper question."
        ),
        "source": (
            "how the case was created: manual, trace, span or sdk. Use contains — it is the "
            "only operator that answers. The column is a ClickHouse Enum8 while "
            "opik-backend declares the filter field as a string, so = and starts_with "
            "compile to lower(source), which ClickHouse cannot apply to an enum and which "
            "fails the request with a 500; contains compiles to ilike, which converts. "
            "Measured against two unrelated datasets, on a valid value as well as an "
            "unknown one. Fixed backend-side by typing the field as the enum it is, the way "
            "TraceField.SOURCE already is — at which point = works and contains stops."
        ),
        "data": (
            "the case's own keys, one per column of the dataset — data.question, "
            "data.expected_output. No comparisons: the column is a ClickHouse Map, and "
            "opik-backend's operator map has no > or < for that type (it answers 400). The "
            "comparison's data.<key> does take them — there the key becomes a field the "
            "backend types as a string."
        ),
    },
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

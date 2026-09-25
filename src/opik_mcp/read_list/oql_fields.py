"""The OQL field and operator tables, taken from opik-backend.

They come from its ``TraceField`` / ``SpanField`` / ``TraceThreadField`` /
``ExperimentField`` enums and its ``FilterQueryBuilder`` operator map, not the
SDK's copies — the SDK lists ``>=`` / ``<=`` for dictionaries (the backend
400s) and misses ``is_empty`` on enums, ``source``, ``error_type``, ``ttft``
and the whole experiment surface.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.read_list.handler import FieldType

# Operator → FieldType entries of opik-backend's ANALYTICS_DB_OPERATOR_MAP
# (FilterQueryBuilder.java). A pair missing there is a 400 server-side.
OPERATORS_BY_TYPE: Final[dict[str, tuple[str, ...]]] = {
    "string": ("=", "!=", "contains", "not_contains", "starts_with", "ends_with", ">", "<"),
    # A key addressed inside a map the user owns: ``data.question`` on a
    # dataset item, ``output.answer`` on the runs compared with it. opik-backend
    # calls these dynamic fields and wants the key spliced into the field name
    # with a declared type, which is what ``oql._dynamic_clause`` writes.
    "keyed_string": ("=", "!=", "contains", "not_contains", "starts_with", "ends_with", ">", "<"),
    "flat_or_keyed_string": (
        "=",
        "!=",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        ">",
        "<",
    ),
    "date_time": ("=", "!=", ">", ">=", "<", "<="),
    "number": ("=", "!=", ">", ">=", "<", "<="),
    "feedback_scores": ("=", "!=", ">", ">=", "<", "<=", "is_empty", "is_not_empty"),
    "dictionary": (
        "=",
        "!=",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        ">",
        "<",
        "is_empty",
        "is_not_empty",
    ),
    # A ClickHouse Map column the user owns: a dataset item's ``data``. The
    # backend reads the key out of the clause's ``key`` (it is a real column,
    # not a dynamic field name) and maps six string operators onto it —
    # ``ANALYTICS_DB_OPERATOR_MAP`` has no MAP entry for a comparison or for
    # emptiness, and ``FiltersFactory`` answers 400 for the pair. The
    # dictionary type above promises what this one cannot deliver.
    "map": ("=", "!=", "contains", "not_contains", "starts_with", "ends_with"),
    "list": ("=", "!=", "contains", "not_contains", "is_empty", "is_not_empty"),
    "enum": ("=", "!=", "in", "not_in", "is_empty", "is_not_empty"),
    "enum_legacy": ("=", "!="),
    "error_container": ("is_empty", "is_not_empty"),
    "string_list": ("in", "not_in"),
}

# Types whose filters need a ``.key`` (the backend rejects a blank key).
KEYED_TYPES: Final = frozenset({"feedback_scores", "dictionary", "map"})
#: Types whose filters carry a key. ``keyed_string`` needs one (``data`` alone
#: is not a field the backend knows); ``flat_or_keyed_string`` takes one or
#: not (``output`` searches the whole body, ``output.answer`` one key of it).
KEY_REQUIRED_TYPES: Final = KEYED_TYPES | {"keyed_string"}
KEY_ALLOWED_TYPES: Final = KEY_REQUIRED_TYPES | {"flat_or_keyed_string"}
#: Types the backend cannot type on its own, because the field name is the
#: user's. The compiled clause declares the type for them and splices the key
#: into the name; everything else is a known field the backend types itself.
DYNAMIC_TYPES: Final = frozenset({"keyed_string", "flat_or_keyed_string"})
USAGE_FIELDS: Final = ("usage.total_tokens", "usage.prompt_tokens", "usage.completion_tokens")
# Number fields the backend stores in milliseconds — named in value errors so
# an agent writing ``duration > 5`` learns it asked for five milliseconds.
MILLISECOND_FIELDS: Final = frozenset({"duration", "ttft"})

#: Timing fields every observability record carries.
TIMING_FIELDS: Final[dict[str, FieldType]] = {
    "start_time": "date_time",
    "end_time": "date_time",
    "created_at": "date_time",
    "last_updated_at": "date_time",
}
#: The payload, usage, cost, score and error fields a trace and a span share.
PAYLOAD_FIELDS: Final[dict[str, FieldType]] = {
    "input": "string",
    "output": "string",
    "input_json": "dictionary",
    "output_json": "dictionary",
    "metadata": "dictionary",
    "tags": "list",
    "usage.total_tokens": "number",
    "usage.prompt_tokens": "number",
    "usage.completion_tokens": "number",
    "total_estimated_cost": "number",
    "duration": "number",
    "ttft": "number",
    "feedback_scores": "feedback_scores",
    "error_info": "error_container",
    "error_type": "string",
    "source": "enum_legacy",
    "environment": "enum",
}


NEGATING_OPERATORS: Final = frozenset({"!=", "not_in"})

PARENT_ID_FIELDS: Final[tuple[str, ...]] = (
    "trace_id",
    "thread_id",
    "experiment_id",
    "experiment_ids",
)
"""A filter on one of these names a parent record, and turns a list into the
rest of that record rather than a triage of the project — so the ``sdk``
default is not added on top of it.

Every experiment trace carries a source other than ``sdk`` (``evaluate`` and
``run_tests`` write ``experiment``, the optimizer writes ``optimization``), so
the default on top of an experiment drill-in hid all of it; the list tool's
``_without_default`` tells that story. Declared here beside the default it
exempts from, because two callers apply that default — the list tool and the
metric runner — and had to agree."""
SDK_SOURCE_CLAUSE: Final[dict[str, str]] = {
    "field": "source",
    "operator": "=",
    "key": "",
    "value": "sdk",
}
"""That default in compiled form, in one place.

It had been written out three times — twice as this dict, once as its JSON
string — and three copies of a default are three chances for the tools to
disagree about what the default is. Copy it before mutating: the compiled
clause lists are built by appending to them."""


__all__ = [
    "DYNAMIC_TYPES",
    "KEYED_TYPES",
    "KEY_ALLOWED_TYPES",
    "KEY_REQUIRED_TYPES",
    "MILLISECOND_FIELDS",
    "NEGATING_OPERATORS",
    "OPERATORS_BY_TYPE",
    "PARENT_ID_FIELDS",
    "PAYLOAD_FIELDS",
    "SDK_SOURCE_CLAUSE",
    "TIMING_FIELDS",
    "USAGE_FIELDS",
]

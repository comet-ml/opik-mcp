"""``schema("list.<entity>")`` — the full filter/sort reference for one entity.

The tool description for ``list`` carries only two lines of grammar; this is
where an agent looks the fields up on purpose. The reference is generated
from the same tables the validator uses, so it cannot drift from what
``list`` accepts.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.oql import FILTERABLE_FIELDS, OPERATORS_BY_TYPE
from opik_mcp.read_list.sorting import sortable_names
from opik_mcp.writes.errors import UnknownOperationError
from opik_mcp.writes.schema_tool import run_schema


def test_list_trace_reference_carries_fields_operators_sort_window_search() -> None:
    ref = run_schema("list.trace")
    assert ref["operation"] == "list.trace"
    assert ref["entity_type"] == "trace"

    duration = ref["filters"]["fields"]["duration"]
    assert duration == {
        "type": "number",
        "operators": ["=", "!=", ">", ">=", "<", "<="],
        "unit": "milliseconds",
    }
    metadata = ref["filters"]["fields"]["metadata"]
    assert metadata["type"] == "dictionary"
    assert metadata["key"] == "required"
    assert ">=" not in metadata["operators"]
    assert ref["filters"]["fields"]["error_info"]["operators"] == ["is_empty", "is_not_empty"]

    assert "<field>[.<key>] <op> <value> [AND ...]" in ref["filters"]["grammar"]
    assert len(ref["filters"]["examples"]) == 2
    assert ref["filters"]["default"] == 'source = "sdk" unless you name source'

    assert ref["sort"]["form"] == "<field> [asc|desc]"
    assert "feedback_scores.<name>" in ref["sort"]["fields"]
    assert ref["window"] is True
    assert ref["search"] is True


def test_list_experiment_reference_has_no_window_search_or_source_default() -> None:
    ref = run_schema("list.experiment")
    assert ref["window"] is False
    assert ref["search"] is False
    assert "default" not in ref["filters"]
    assert set(ref["filters"]["fields"]) == set(FILTERABLE_FIELDS["experiment"])


@pytest.mark.parametrize("entity_type", ["trace", "span", "thread", "experiment"])
def test_reference_matches_the_validator_tables_exactly(entity_type: str) -> None:
    ref = run_schema(f"list.{entity_type}")
    fields = ref["filters"]["fields"]
    assert set(fields) == set(FILTERABLE_FIELDS[entity_type])
    for name, spec in fields.items():
        expected_type = FILTERABLE_FIELDS[entity_type][name]
        assert spec["type"] == expected_type
        assert spec["operators"] == list(OPERATORS_BY_TYPE[expected_type])
    assert ref["sort"]["fields"] == sortable_names(entity_type)


def test_unknown_list_key_recovers_with_the_list_keys_listed() -> None:
    with pytest.raises(ToolError) as ei:
        run_schema("list.widget")
    assert isinstance(ei.value.__cause__, UnknownOperationError)
    assert "list.trace" in str(ei.value)
    assert "score.create" in str(ei.value)

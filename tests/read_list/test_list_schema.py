"""``schema("list.<entity>")`` — the full filter/sort reference for one entity.

The tool description for ``list`` carries only two lines of grammar; this is
where an agent looks the fields up on purpose. The reference is generated
from the same tables the validator uses, so it cannot drift from what
``list`` accepts.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.oql import OPERATORS_BY_TYPE
from opik_mcp.read_list.registry import VOCABULARIES
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
    assert set(ref["filters"]["fields"]) == set(VOCABULARIES["experiment"].filter_fields)


def test_a_field_the_backend_takes_as_a_parameter_lists_only_what_compiles() -> None:
    """The reference is the accepted set, not the field type's set.

    ``type`` is an enum and ``optimization_id`` a string, so the type tables
    would offer ``!=``, ``not_in``, ``contains`` and the rest — every one of
    which the compiler refuses, because the query parameter behind them
    cannot carry it. Advertising them here is the exact failure the values
    list was added to prevent, one field over.
    """
    fields = run_schema("list.experiment")["filters"]["fields"]
    assert fields["type"]["operators"] == ["=", "in"]
    assert fields["optimization_id"]["operators"] == ["="]
    assert fields["optimization_id"]["format"] == "UUID"
    assert fields["experiment_ids"]["operators"] == ["in"]
    assert fields["experiment_ids"]["format"] == "UUID"


def test_experiment_prompt_filter_says_it_matches_prompts_not_versions() -> None:
    """The field is named for ids and matches prompts, which is the kind of
    gap a caller cannot see from the name. The backend concatenates the
    legacy prompt id with the keys of the prompt-versions map, and those
    keys are prompt ids — nothing filters by prompt version id at all. A
    caller who assumes otherwise gets a broader page that looks exact."""
    ref = run_schema("list.experiment")
    note = ref["filters"]["fields"]["prompt_ids"]["note"]
    assert "prompt ids" in note
    assert "version" in note


@pytest.mark.parametrize("entity_type", ["trace", "span", "thread", "experiment"])
def test_reference_matches_the_validator_tables_exactly(entity_type: str) -> None:
    ref = run_schema(f"list.{entity_type}")
    fields = ref["filters"]["fields"]
    vocabulary = VOCABULARIES[entity_type]
    assert set(fields) == set(vocabulary.filter_fields)
    for name, spec in fields.items():
        expected_type = vocabulary.filter_fields[name]
        assert spec["type"] == expected_type
        # A field the backend takes as a query parameter accepts less than
        # its type does, and the reference has to say the smaller set — that
        # is what the caller is held to.
        param = vocabulary.param_fields.get(name)
        expected_ops = (
            list(param.operators) if param is not None else list(OPERATORS_BY_TYPE[expected_type])
        )
        assert spec["operators"] == expected_ops
    assert ref["sort"]["fields"] == sortable_names(VOCABULARIES[entity_type])


def test_unknown_list_key_recovers_with_the_list_keys_listed() -> None:
    with pytest.raises(ToolError) as ei:
        run_schema("list.widget")
    assert isinstance(ei.value.__cause__, UnknownOperationError)
    assert "list.trace" in str(ei.value)
    assert "score.create" in str(ei.value)


def test_reference_lists_the_values_a_closed_enum_accepts() -> None:
    """The compiler refuses an unknown enum value, so the accepted set has to
    be discoverable here — otherwise the only way to learn it is to be
    rejected."""
    fields = run_schema("list.span")["filters"]["fields"]
    assert fields["type"]["values"] == ["general", "tool", "llm", "guardrail", "unknown"]
    assert fields["source"]["values"][0] == "sdk"
    # Open-ended: any deployment names its own environments, so no list.
    assert "values" not in fields["environment"]


@pytest.mark.parametrize("entity_type", sorted(VOCABULARIES))
def test_every_enum_value_table_names_a_field_of_its_entity(entity_type: str) -> None:
    """A value list for a field the entity does not have would never fire."""
    vocabulary = VOCABULARIES[entity_type]
    for field in vocabulary.enum_values:
        assert field in vocabulary.filter_fields

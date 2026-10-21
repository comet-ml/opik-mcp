"""OQL — the Opik Query Language behind the ``list`` tool's ``filters`` string.

``oql_parser`` holds the grammar and ``oql_fields`` the field and operator
tables; this module checks parsed clauses against an entity's vocabulary and
compiles them into the backend's filter array. Unknown fields are rejected
here instead of being defaulted to ``string`` and left for the backend to
refuse, a departure from the SDK.

Every problem in a string is collected and reported together so the agent
repairs the call in one retry: syntax errors carry the position and a caret,
unknown fields carry the closest name and the entity's field list, invalid
operators carry the field's type and the valid set, invalid values carry the
expected format. The full type/operator reference lives behind
``schema("list.<entity>")``, keeping the tool description short.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import FieldType, ParamField, Vocabulary
from opik_mcp.read_list.oql_fields import (
    DYNAMIC_TYPES,
    KEY_ALLOWED_TYPES,
    KEY_REQUIRED_TYPES,
    MILLISECOND_FIELDS,
    NEGATING_OPERATORS,
    OPERATORS_BY_TYPE,
    USAGE_FIELDS,
)
from opik_mcp.read_list.oql_parser import (
    GRAMMAR_LINE,
    LIST_VALUE_OPERATORS,
    NO_VALUE_OPERATORS,
    ParseError,
    Parser,
    RawClause,
    clauses_before_error,
)
from opik_mcp.read_list.uri import is_uuid
from opik_mcp.read_list.window import parse_instant

IssueKind = Literal["syntax", "unknown_field", "bad_operator", "bad_value", "unsupported_entity"]


@dataclass(frozen=True)
class OQLIssue:
    kind: IssueKind
    message: str
    position: int | None = None
    """Cursor offset into the query for syntax errors; None for semantic ones."""


class OQLError(EntityArgValidationError):
    """The ``filters`` string does not validate. Carries every issue found.

    Subclasses ``EntityArgValidationError`` so the analytics wrapper buckets
    it as validation/400 like the other list-argument failures. Instantiating
    ``OQLError`` yields the per-kind subclass of the first issue (``OQLSyntaxError``
    etc.): analytics records only exception class names, so the class is how
    "which kind of mistake do agents make" reaches a dashboard without the
    string itself ever leaving the process.
    """

    def __new__(cls, _vocabulary: Vocabulary, _query: str, issues: list[OQLIssue]) -> OQLError:
        if cls is OQLError and issues:
            cls = _KIND_CLASSES[issues[0].kind]
        return super().__new__(cls)

    def __init__(self, vocabulary: Vocabulary, query: str, issues: list[OQLIssue]) -> None:
        self.vocabulary = vocabulary
        self.query = query
        self.issues: tuple[OQLIssue, ...] = tuple(issues)
        super().__init__(self._render())

    @property
    def kinds(self) -> tuple[IssueKind, ...]:
        return tuple(i.kind for i in self.issues)

    def _render(self) -> str:
        lines = [f"Invalid filters for {self.vocabulary.entity_type}:"]
        for n, issue in enumerate(self.issues, start=1):
            lines.append(f"  {n}. {issue.message}")
            if issue.position is not None:
                lines.append(self.query)
                lines.append(" " * issue.position + "^")
        lines.append(f"Grammar: {GRAMMAR_LINE}")
        if self.vocabulary.filter_fields:
            lines.append(f'Field reference: schema("list.{self.vocabulary.name}").')
        return "\n".join(lines)


class OQLSyntaxError(OQLError):
    """Grammar problem: quoting, operator shape, trailing text, OR."""


class OQLUnknownFieldError(OQLError):
    """A field the entity does not have."""


class OQLBadOperatorError(OQLError):
    """An operator the field's type does not support."""


class OQLBadValueError(OQLError):
    """A value in the wrong format, or a missing key on a keyed field."""


class OQLUnsupportedEntityError(OQLError):
    """``filters`` on an entity type that has none."""


_KIND_CLASSES: Final[dict[IssueKind, type[OQLError]]] = {
    "syntax": OQLSyntaxError,
    "unknown_field": OQLUnknownFieldError,
    "bad_operator": OQLBadOperatorError,
    "bad_value": OQLBadValueError,
    "unsupported_entity": OQLUnsupportedEntityError,
}


def _validate(
    vocabulary: Vocabulary, raw: RawClause
) -> tuple[dict[str, str] | None, OQLIssue | None]:
    fields = vocabulary.filter_fields
    field, key = raw.field, raw.key

    # usage.<x> is a flat composite field name, not a dictionary key.
    if field == "usage":
        composite = f"usage.{key}" if key is not None else "usage"
        if composite not in fields:
            known = [one for one in USAGE_FIELDS if one in fields]
            if known:
                return None, OQLIssue(
                    "unknown_field",
                    f"Unknown field '{composite}'. Usage fields: {', '.join(known)}.",
                )
            return None, _unknown_field(vocabulary, composite)
        field, key = composite, None

    if field not in fields:
        return None, _unknown_field(vocabulary, field)

    ftype = fields[field]
    if key is not None and ftype not in KEY_ALLOWED_TYPES:
        keyed = ", ".join(sorted(f for f, t in fields.items() if t in KEY_ALLOWED_TYPES))
        return None, OQLIssue(
            "unknown_field",
            f"Field '{field}' does not take a key ('{field}.{key}'). Keyed fields: {keyed}.",
        )

    spec = vocabulary.param_fields.get(field)
    if spec is not None and raw.operator not in spec.operators:
        # Checked before the type's own operator set, and instead of it: the
        # parameter's shape is the tighter rule and the one the caller has to
        # hear about. ``type != "trial"`` is a valid enum operator refused for
        # a reason that has nothing to do with enums.
        return None, _param_operator_issue(vocabulary, field, spec, raw)

    valid_ops = OPERATORS_BY_TYPE[ftype]
    if raw.operator not in valid_ops:
        return None, OQLIssue(
            "bad_operator",
            f"Operator '{raw.operator}' is not valid for '{field}' ({ftype}). "
            f"Valid: {', '.join(valid_ops)}.",
        )

    issue = _validate_value(vocabulary, field, ftype, key, raw)
    if issue is not None:
        return None, issue
    if ftype in DYNAMIC_TYPES:
        return _dynamic_clause(field, key, raw), None
    return {"field": field, "operator": raw.operator, "key": key or "", "value": raw.value}, None


def _dynamic_clause(field: str, key: str | None, raw: RawClause) -> dict[str, str]:
    """A clause on a field whose name the user chose.

    opik-backend types its own known fields and refuses a dynamic one that
    arrives untyped, so the type rides along. The key is spliced into the name
    (``data`` + ``question`` → ``data.question``) because that is the field
    name on the wire, not a key beside it the way a score name is.
    """
    return {
        "field": f"{field}.{key}" if key else field,
        "type": "string",
        "operator": raw.operator,
        "key": "",
        "value": raw.value,
    }


def operand_values(operator: str, value: str) -> list[str]:
    """The values a clause names. ``in``/``not_in`` carry them comma-joined;
    everything else names exactly one."""
    return value.split(",") if operator in LIST_VALUE_OPERATORS else [value]


def _param_operator_issue(
    vocabulary: Vocabulary, field: str, spec: ParamField, raw: RawClause
) -> OQLIssue:
    """Refuse an operator the query parameter cannot carry — with the query
    that would have worked, whenever one can be computed.

    A negation over a closed set has an exact rewrite, so naming the valid
    operators and stopping would leave the caller to enumerate the rest of
    the set themselves. Everything else falls back to naming them.
    """
    base = (
        f"Operator '{raw.operator}' is not valid for '{field}' on "
        f"{vocabulary.entity_type}: {spec.why}."
    )
    values = vocabulary.enum_values.get(field)
    if values is not None and raw.operator in NEGATING_OPERATORS:
        excluded = set(operand_values(raw.operator, raw.value))
        rest = [v for v in values if v not in excluded]
        if rest:
            items = ", ".join(_quote(v) for v in rest)
            return OQLIssue("bad_operator", f"{base} Write: {field} in ({items})")
    return OQLIssue("bad_operator", f"{base} Valid: {', '.join(spec.operators)}.")


def _unknown_field(vocabulary: Vocabulary, field: str) -> OQLIssue:
    names = sorted(vocabulary.filter_fields)
    close = difflib.get_close_matches(field, names, n=1, cutoff=0.6)
    hint = f" Did you mean '{close[0]}'?" if close else ""
    for ignored, why in vocabulary.ignored_fields:
        if field in ignored:
            hint = f" {why}"
            break
    return OQLIssue("unknown_field", f"Unknown field '{field}'.{hint} Fields: {', '.join(names)}.")


def _validate_value(
    vocabulary: Vocabulary, field: str, ftype: str, key: str | None, raw: RawClause
) -> OQLIssue | None:
    if ftype in KEY_REQUIRED_TYPES and not key:
        if ftype == "feedback_scores":
            return OQLIssue(
                "bad_value",
                f"'{field}' needs a score name: write {field}.<name>, e.g. {field}.accuracy < 0.5.",
            )
        if ftype in ("keyed_string", "map"):
            return OQLIssue(
                "bad_value",
                f"'{field}' needs a key: write {field}.<key>, "
                f'e.g. {field}.question contains "refund".',
            )
        return OQLIssue(
            "bad_value",
            f"'{field}' needs a key: write {field}.<key>, e.g. {field}.environment = \"prod\".",
        )
    if raw.operator in NO_VALUE_OPERATORS:
        return None
    if ftype == "date_time":
        if parse_instant(raw.value) is None:
            return OQLIssue(
                "bad_value",
                f"Invalid value \"{raw.value}\" for '{field}': expected an ISO-8601 instant "
                'with a timezone, e.g. "2026-09-08T10:00:00Z".',
            )
        return None
    if ftype in ("number", "feedback_scores"):
        try:
            float(raw.value)
        except ValueError:
            unit = " (value is in milliseconds)" if field in MILLISECOND_FIELDS else ""
            return OQLIssue(
                "bad_value",
                f"Invalid value \"{raw.value}\" for '{field}': expected a number{unit}.",
            )
        return None
    if raw.value.strip() == "":
        return OQLIssue(
            "bad_value", f"Empty value for '{field}': the backend rejects blank filter values."
        )
    spec = vocabulary.param_fields.get(field)
    if spec is not None and spec.value_form == "uuid":
        bad = [v for v in operand_values(raw.operator, raw.value) if not is_uuid(v)]
        if bad:
            return OQLIssue(
                "bad_value",
                f"Invalid value{'s' if len(bad) > 1 else ''} "
                f"{', '.join(repr(v) for v in bad)} for '{field}': expected a UUID, which is "
                f"what the backend's '{spec.param}' parameter takes.",
            )
    allowed = vocabulary.enum_values.get(field)
    if allowed is not None:
        # ``in``/``not_in`` values arrive comma-joined; one bad element fails
        # the whole filter server-side, so every element is checked.
        bad = [v for v in operand_values(raw.operator, raw.value) if v not in allowed]
        if bad:
            return OQLIssue(
                "bad_value",
                f"Invalid value{'s' if len(bad) > 1 else ''} "
                f"{', '.join(repr(v) for v in bad)} for '{field}': "
                f"expected one of {', '.join(allowed)}.",
            )
    return None


def compile_filters(
    vocabulary: Vocabulary, query: str, *, filterable_types: Sequence[str] = ()
) -> list[dict[str, str]]:
    """Compile an OQL string into the backend's filter array.

    Returns ``[{field, operator, key, value}, …]`` ready to be JSON-encoded into
    the ``filters`` query parameter. An empty or blank query compiles to ``[]``.
    Raises :class:`OQLError` carrying every problem found. ``filterable_types``
    is named in the refusal for a vocabulary with no filter fields.
    """
    if not vocabulary.filter_fields:
        raise OQLError(
            vocabulary,
            query,
            [
                OQLIssue(
                    "unsupported_entity",
                    f"filters are not supported for {vocabulary.name!r}. "
                    f"Filterable types: {', '.join(filterable_types)}.",
                )
            ],
        )

    parser = Parser(query)
    issues: list[OQLIssue] = []
    compiled: list[dict[str, str]] = []
    try:
        raw_clauses = parser.parse()
    except ParseError as e:
        # Clauses parsed before the syntax error still get validated so the
        # agent sees every problem in one round.
        raw_clauses = []
        for raw in clauses_before_error(parser):
            clause, issue = _validate(vocabulary, raw)
            if issue is not None:
                issues.append(issue)
        issues.append(
            OQLIssue("syntax", f"Syntax error at position {e.position}: {e.message}", e.position)
        )
        raise OQLError(vocabulary, query, issues) from None

    for raw in raw_clauses:
        clause, issue = _validate(vocabulary, raw)
        if issue is not None:
            issues.append(issue)
        elif clause is not None:
            compiled.append(clause)
    if issues:
        raise OQLError(vocabulary, query, issues)
    return compiled


def filter_field_names(
    entity_type: str, query: str | None, vocabularies: Iterable[Vocabulary]
) -> list[str]:
    """Sorted, de-duplicated field names a query uses — keys stripped.

    For analytics: says *which fields* agents filter on without carrying the
    values or the user-named keys. Returns ``[]`` for anything that does not
    parse; the failure itself is recorded by exception class elsewhere.
    """
    if not query:
        return []
    # An entity with more than one vocabulary is filtered through whichever
    # one the call chose, and this function is handed the ``entity_type`` the
    # caller typed rather than the mode. Trying each keeps the dashboard from
    # reading a valid filter as an unparseable one.
    for vocabulary in vocabularies:
        typed = entity_type in (vocabulary.name, vocabulary.entity_type)
        if not typed or not vocabulary.filter_fields:
            continue
        try:
            clauses = compile_filters(vocabulary, query)
        except OQLError:
            continue
        # A dynamic field carries the user's key spliced into its name
        # (``data`` + ``question``), which is exactly what this function
        # promises not to hand to a dashboard. Cut back to the field.
        return sorted({c["field"].partition(".")[0] for c in clauses})
    return []


def split_param_clauses(
    vocabulary: Vocabulary, clauses: list[dict[str, str]]
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Lift the clauses the backend wants as query parameters out of the array.

    Returns the clauses that still travel in ``filters`` and the query
    parameters the rest became. The caller keeps the *whole* compiled list for
    the applied-filters header — a clause that narrowed the page and went
    unmentioned would make the header under-report what was applied.

    A separate pass rather than part of :func:`compile_filters`, so that
    compiling stays a pure function of the query string: the metric runner
    compiles filters it never sends as parameters, and the compiled shape is
    what the OQL tests assert against.
    """
    specs = vocabulary.param_fields
    if not specs:
        return clauses, {}

    remaining: list[dict[str, str]] = []
    params: dict[str, str] = {}
    for clause in clauses:
        spec = specs.get(clause["field"])
        if spec is None:
            remaining.append(clause)
            continue
        if spec.param in params:
            # One parameter cannot carry two AND-ed clauses: merging their
            # values into one set would turn the AND into an OR and answer a
            # question nobody asked.
            raise OQLError(
                vocabulary,
                render_filters(vocabulary, clauses),
                [
                    OQLIssue(
                        "bad_operator",
                        f"'{clause['field']}' can appear only once: the backend takes it as a "
                        f"single '{spec.param}' parameter, so two clauses cannot both apply.",
                    )
                ],
            )
        values = operand_values(clause["operator"], clause["value"])
        params[spec.param] = (
            json.dumps(values, separators=(",", ":")) if spec.encoding == "json_list" else values[0]
        )
    return remaining, params


def render_filters(vocabulary: Vocabulary, clauses: list[dict[str, str]]) -> str:
    """Render a compiled filter array back to OQL, for the applied-filters header.

    Numbers on numeric fields render bare, everything else double-quoted, so
    the echoed string is itself valid input for the next call.
    """
    fields: Mapping[str, FieldType] = vocabulary.filter_fields
    parts: list[str] = []
    for c in clauses:
        field, op, key, value = c["field"], c["operator"], c.get("key", ""), c.get("value", "")
        name = f"{field}.{_quote_key(key)}" if key else field
        if op in NO_VALUE_OPERATORS:
            parts.append(f"{name} {op}")
        elif op in LIST_VALUE_OPERATORS:
            items = ", ".join(_quote(v) for v in operand_values(op, value))
            parts.append(f"{name} {op} ({items})")
        elif fields.get(field) in ("number", "feedback_scores"):
            parts.append(f"{name} {op} {value}")
        else:
            parts.append(f"{name} {op} {_quote(value)}")
    return " AND ".join(parts)


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_key(key: str) -> str:
    return key if all(char.isalnum() or char == "_" for char in key) else _quote(key)


__all__ = [
    "IssueKind",
    "OQLBadOperatorError",
    "OQLBadValueError",
    "OQLError",
    "OQLIssue",
    "OQLSyntaxError",
    "OQLUnknownFieldError",
    "OQLUnsupportedEntityError",
    "compile_filters",
    "filter_field_names",
    "operand_values",
    "render_filters",
    "split_param_clauses",
]

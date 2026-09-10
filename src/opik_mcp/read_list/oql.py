"""OQL — the Opik Query Language behind the ``list`` tool's ``filters`` string.

Grammar (the same one the Opik Python SDK's ``search_traces(filter_string=…)``
accepts, so agents learn one query form)::

    <field>[.<key>] <op> <value> [AND <field> <op> <value>]*

- Connector is ``AND`` only. ``OR`` is rejected with an explicit message.
- Values are double-quoted strings (``""`` escapes a quote) or bare numbers.
- ``is_empty`` / ``is_not_empty`` take no value.
- ``in`` / ``not_in`` take a parenthesised list of quoted strings.
- ``usage.total_tokens`` / ``usage.prompt_tokens`` / ``usage.completion_tokens``
  are flat field names, not dictionary keys.

The parser is ported from the SDK (``opik/api_objects/opik_query_language.py``)
with two deliberate departures. First, the field and operator tables come
from opik-backend's ``TraceField`` / ``SpanField`` / ``TraceThreadField`` /
``ExperimentField`` enums and its ``FilterQueryBuilder`` operator map, not the
SDK's copies — the SDK lists ``>=`` / ``<=`` for dictionaries (the backend
400s) and misses ``is_empty`` on enums, ``source``, ``error_type``, ``ttft``
and the whole experiment surface. Second, unknown fields are rejected here
instead of being defaulted to ``string`` and left for the backend to refuse.

Every problem in a string is collected and reported together so the agent
repairs the call in one retry: syntax errors carry the position and a caret,
unknown fields carry the closest name and the entity's field list, invalid
operators carry the field's type and the valid set, invalid values carry the
expected format. The full type/operator reference lives behind
``schema("list.<entity>")``, keeping the tool description short.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Final, Literal

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.window import parse_instant

FieldType = Literal[
    "string",
    "date_time",
    "number",
    "feedback_scores",
    "dictionary",
    "list",
    "enum",
    "enum_legacy",
    "error_container",
    "string_list",
]

# Operator → FieldType entries of opik-backend's ANALYTICS_DB_OPERATOR_MAP
# (FilterQueryBuilder.java). A pair missing there is a 400 server-side.
OPERATORS_BY_TYPE: Final[dict[str, tuple[str, ...]]] = {
    "string": ("=", "!=", "contains", "not_contains", "starts_with", "ends_with", ">", "<"),
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
    "list": ("=", "!=", "contains", "not_contains", "is_empty", "is_not_empty"),
    "enum": ("=", "!=", "in", "not_in", "is_empty", "is_not_empty"),
    "enum_legacy": ("=", "!="),
    "error_container": ("is_empty", "is_not_empty"),
    "string_list": ("in", "not_in"),
}

NO_VALUE_OPERATORS: Final = frozenset({"is_empty", "is_not_empty"})
LIST_VALUE_OPERATORS: Final = frozenset({"in", "not_in"})
# Types whose filters need a ``.key`` (the backend rejects a blank key).
KEYED_TYPES: Final = frozenset({"feedback_scores", "dictionary"})
USAGE_FIELDS: Final = ("usage.total_tokens", "usage.prompt_tokens", "usage.completion_tokens")
# Number fields the backend stores in milliseconds — named in value errors so
# an agent writing ``duration > 5`` learns it asked for five milliseconds.
MILLISECOND_FIELDS: Final = frozenset({"duration", "ttft"})

_SHARED_TIMING: dict[str, FieldType] = {
    "start_time": "date_time",
    "end_time": "date_time",
    "created_at": "date_time",
    "last_updated_at": "date_time",
}
_SHARED_PAYLOAD: dict[str, FieldType] = {
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

# Filterable fields per entity — mirrors the backend's field enums.
FILTERABLE_FIELDS: Final[dict[str, dict[str, FieldType]]] = {
    "trace": {
        "id": "string",
        "name": "string",
        **_SHARED_TIMING,
        **_SHARED_PAYLOAD,
        "llm_span_count": "number",
        "span_feedback_scores": "feedback_scores",
        "thread_id": "string",
        "guardrails": "string",
        "visibility_mode": "enum",
        "annotation_queue_ids": "list",
        "experiment_id": "string",
        "experiment_ids": "string_list",
    },
    "span": {
        "id": "string",
        "name": "string",
        "type": "enum",
        "trace_id": "string",
        **_SHARED_TIMING,
        **_SHARED_PAYLOAD,
        "model": "string",
        "provider": "string",
    },
    "thread": {
        "id": "string",
        "first_message": "string",
        "last_message": "string",
        "number_of_messages": "number",
        "duration": "number",
        **_SHARED_TIMING,
        "feedback_scores": "feedback_scores",
        "status": "enum",
        "tags": "list",
        "annotation_queue_ids": "list",
        "source": "enum_legacy",
        "environment": "enum",
    },
    "experiment": {
        "metadata": "dictionary",
        "dataset_id": "string",
        "project_id": "string",
        "prompt_ids": "list",
        "tags": "list",
        "feedback_scores": "feedback_scores",
        "experiment_scores": "feedback_scores",
    },
}
# Closed enum values, per entity, from opik-backend's own enums (Source,
# SpanType, TraceThreadStatus, VisibilityMode), confirmed against a live
# backend rather than read off the Java alone.
#
# ``source`` is the one the backend itself validates: it deserializes the
# filter value into the enum and throws on a miss, which reaches the caller as
# a 500, not a 400, so `source = "SDK"` is an opaque server error for a
# capital letter. The others are compared as strings in ClickHouse and answer
# 200 with nothing — a silent empty page that reads like "no matches" when it
# really means "no such value". Both are worth refusing here, with the set.
#
# ``unknown`` is included where the column can actually hold it: Source and
# SpanType both define it as a stored value that cannot be ingested (rows that
# predate the field), so filtering for it is a real question. VisibilityMode
# and TraceThreadStatus define no such sentinel.
#
# Only genuinely closed sets appear. ``environment`` is an enum to the operator
# map but a free string in the data — any deployment names its own — so listing
# values would reject valid filters.
_SOURCE_VALUES: Final = ("sdk", "experiment", "playground", "optimization", "evaluator", "unknown")

ENUM_VALUES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "trace": {
        "source": _SOURCE_VALUES,
        "visibility_mode": ("default", "hidden"),
    },
    "span": {
        "source": _SOURCE_VALUES,
        "type": ("general", "tool", "llm", "guardrail", "unknown"),
    },
    "thread": {
        "source": _SOURCE_VALUES,
        "status": ("active", "inactive"),
    },
}

SUPPORTED_ENTITIES: Final[tuple[str, ...]] = tuple(FILTERABLE_FIELDS)
# The per-entity search surface beyond filters, in one place so the list tool
# and the schema reference cannot disagree. Experiments have no ``source``
# (they are one source by definition), no time window and no free-text search.
SOURCE_DEFAULTED_ENTITIES: Final[tuple[str, ...]] = ("trace", "span", "thread")
"""Lists that add ``source = "sdk"`` unless the caller names ``source`` — the
UI's Logs page default, so evaluator / playground / experiment traces don't
crowd out application traffic."""
WINDOWED_ENTITIES: Final[tuple[str, ...]] = ("trace", "span", "thread")
"""Lists whose backend endpoint takes ``from_time``/``to_time`` and free-text
``search`` (the two capabilities ship together on the backend)."""

GRAMMAR_LINE: Final = (
    "<field>[.<key>] <op> <value> [AND ...] — strings in double quotes, numbers bare, "
    'is_empty/is_not_empty take no value, in/not_in take ("a", "b").'
)

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

    def __new__(cls, entity_type: str, query: str, issues: list[OQLIssue]) -> OQLError:
        if cls is OQLError and issues:
            cls = _KIND_CLASSES[issues[0].kind]
        return super().__new__(cls)

    def __init__(self, entity_type: str, query: str, issues: list[OQLIssue]) -> None:
        self.entity_type = entity_type
        self.query = query
        self.issues: tuple[OQLIssue, ...] = tuple(issues)
        super().__init__(self._render())

    @property
    def kinds(self) -> tuple[IssueKind, ...]:
        return tuple(i.kind for i in self.issues)

    def _render(self) -> str:
        lines = [f"Invalid filters for {self.entity_type}:"]
        for n, issue in enumerate(self.issues, start=1):
            lines.append(f"  {n}. {issue.message}")
            if issue.position is not None:
                lines.append(self.query)
                lines.append(" " * issue.position + "^")
        lines.append(f"Grammar: {GRAMMAR_LINE}")
        if self.entity_type in FILTERABLE_FIELDS:
            lines.append(f'Field reference: schema("list.{self.entity_type}").')
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


@dataclass(frozen=True)
class _RawClause:
    field: str
    key: str | None
    operator: str
    value: str
    """Comma-joined for in/not_in, "" for is_empty/is_not_empty."""
    quoted: bool
    """True when the value was written in double quotes (vs a bare number)."""
    field_position: int


class _SyntaxError(Exception):
    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        self.message = message
        self.position = position


class _Parser:
    """Character-cursor parser ported from the SDK, minus the semantic checks.

    Produces raw clauses; ``_validate`` applies the field/operator/value
    tables afterwards so semantic problems in several clauses can all be
    reported at once. Syntax problems stop the parse (there is no reliable
    way to resynchronise) and are reported with the cursor position.
    """

    def __init__(self, query: str) -> None:
        self.q = query
        self.i = 0

    # -- helpers --

    def _at_end(self) -> bool:
        return self.i >= len(self.q)

    def _skip_ws(self) -> None:
        while not self._at_end() and self.q[self.i].isspace():
            self.i += 1

    def _read_word(self) -> str:
        start = self.i
        while not self._at_end() and (self.q[self.i].isalnum() or self.q[self.i] == "_"):
            self.i += 1
        return self.q[start : self.i]

    def _read_quoted(self, *, what: str) -> str:
        """Cursor is on the opening quote. Returns the unescaped content."""
        open_pos = self.i
        self.i += 1
        out: list[str] = []
        while not self._at_end():
            ch = self.q[self.i]
            if ch == '"':
                if self.i + 1 < len(self.q) and self.q[self.i + 1] == '"':
                    out.append('"')
                    self.i += 2
                    continue
                self.i += 1
                return "".join(out)
            out.append(ch)
            self.i += 1
        raise _SyntaxError(f"missing closing quote for {what} {self.q[open_pos:]}", open_pos)

    def _read_number(self) -> str:
        start = self.i
        if not self._at_end() and self.q[self.i] == "-":
            self.i += 1
        digits_start = self.i
        while not self._at_end() and self.q[self.i].isdigit():
            self.i += 1
        if self.i == digits_start:
            msg = "expected a number after '-'" if self.q[start] == "-" else "expected a number"
            raise _SyntaxError(msg, start)
        if not self._at_end() and self.q[self.i] == ".":
            self.i += 1
            frac_start = self.i
            while not self._at_end() and self.q[self.i].isdigit():
                self.i += 1
            if self.i == frac_start:
                raise _SyntaxError("expected digits after decimal point", frac_start)
        return self.q[start : self.i]

    # -- grammar --

    def _parse_field(self) -> tuple[str, str | None, int]:
        self._skip_ws()
        pos = self.i
        if self._at_end():
            raise _SyntaxError("unexpected end of input, expected a field name", pos)
        field = self._read_word()
        if not field:
            raise _SyntaxError(f"expected a field name, got {self.q[pos : pos + 12]!r}", pos)
        key: str | None = None
        if not self._at_end() and self.q[self.i] == ".":
            self.i += 1
            if not self._at_end() and self.q[self.i] == '"':
                key = self._read_quoted(what="key")
            else:
                key_pos = self.i
                key = self._read_word()
                if not key:
                    raise _SyntaxError("expected a key after '.'", key_pos)
        return field, key, pos

    def _parse_operator(self) -> str:
        self._skip_ws()
        pos = self.i
        if self._at_end():
            raise _SyntaxError("unexpected end of input, expected an operator", pos)
        ch = self.q[self.i]
        if ch == "=":
            self.i += 1
            return "="
        if ch in "<>!":
            if self.i + 1 < len(self.q) and self.q[self.i + 1] == "=":
                self.i += 2
                return ch + "="
            if ch == "!":
                raise _SyntaxError("expected '!=' ", pos)
            self.i += 1
            return ch
        op = self._read_word()
        if not op:
            raise _SyntaxError(f"expected an operator, got {self.q[pos : pos + 12]!r}", pos)
        return op

    def _parse_value(self) -> tuple[str, bool]:
        self._skip_ws()
        pos = self.i
        if self._at_end():
            raise _SyntaxError("unexpected end of input, expected a value", pos)
        ch = self.q[self.i]
        if ch == '"':
            return self._read_quoted(what="value"), True
        if ch.isdigit() or ch == "-":
            return self._read_number(), False
        raise _SyntaxError('expected a value in double quotes ("…") or a number', pos)

    def _parse_list_value(self) -> str:
        self._skip_ws()
        pos = self.i
        if self._at_end() or self.q[self.i] != "(":
            raise _SyntaxError(
                'expected a list in parentheses after in/not_in, e.g. in ("a", "b"); '
                "the list must start with '('",
                pos,
            )
        self.i += 1
        items: list[str] = []
        while True:
            self._skip_ws()
            if self._at_end():
                raise _SyntaxError("unterminated list, missing ')'", pos)
            if self.q[self.i] == ")":
                if not items:
                    raise _SyntaxError("expected at least one item inside (...)", pos)
                self.i += 1
                return ",".join(items)
            if items:
                if self.q[self.i] != ",":
                    raise _SyntaxError("expected ',' between list items", self.i)
                self.i += 1
                self._skip_ws()
            if self._at_end() or self.q[self.i] != '"':
                raise _SyntaxError("list items must be quoted strings", self.i)
            items.append(self._read_quoted(what="list item"))

    def parse(self) -> list[_RawClause]:
        clauses: list[_RawClause] = []
        self._skip_ws()
        if self._at_end():
            return clauses
        while True:
            field, key, field_pos = self._parse_field()
            operator = self._parse_operator()
            if operator in NO_VALUE_OPERATORS:
                value, quoted = "", False
            elif operator in LIST_VALUE_OPERATORS:
                value, quoted = self._parse_list_value(), True
            else:
                value, quoted = self._parse_value()
            clauses.append(_RawClause(field, key, operator, value, quoted, field_pos))

            self._skip_ws()
            if self._at_end():
                return clauses
            pos = self.i
            connector = self._read_word()
            if connector.lower() == "and":
                continue
            if connector.lower() == "or":
                raise _SyntaxError(
                    "OR is not supported; use AND, or run one query per alternative", pos
                )
            raise _SyntaxError(f"trailing characters {self.q[pos:]!r}", pos)


def _validate(entity_type: str, raw: _RawClause) -> tuple[dict[str, str] | None, OQLIssue | None]:
    fields = FILTERABLE_FIELDS[entity_type]
    field, key = raw.field, raw.key

    # usage.<x> is a flat composite field name, not a dictionary key.
    if field == "usage":
        composite = f"usage.{key}" if key is not None else "usage"
        if composite not in fields:
            return None, OQLIssue(
                "unknown_field",
                f"Unknown field '{composite}'. Usage fields: {', '.join(USAGE_FIELDS)}.",
            )
        field, key = composite, None

    if field not in fields:
        names = sorted(fields)
        close = difflib.get_close_matches(field, names, n=1, cutoff=0.6)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        return None, OQLIssue(
            "unknown_field", f"Unknown field '{field}'.{hint} Fields: {', '.join(names)}."
        )

    ftype = fields[field]
    if key is not None and ftype not in KEYED_TYPES:
        keyed = ", ".join(sorted(f for f, t in fields.items() if t in KEYED_TYPES))
        return None, OQLIssue(
            "unknown_field",
            f"Field '{field}' does not take a key ('{field}.{key}'). Keyed fields: {keyed}.",
        )

    valid_ops = OPERATORS_BY_TYPE[ftype]
    if raw.operator not in valid_ops:
        return None, OQLIssue(
            "bad_operator",
            f"Operator '{raw.operator}' is not valid for '{field}' ({ftype}). "
            f"Valid: {', '.join(valid_ops)}.",
        )

    issue = _validate_value(entity_type, field, ftype, key, raw)
    if issue is not None:
        return None, issue
    return {"field": field, "operator": raw.operator, "key": key or "", "value": raw.value}, None


def _validate_value(
    entity_type: str, field: str, ftype: str, key: str | None, raw: _RawClause
) -> OQLIssue | None:
    if ftype in KEYED_TYPES and not key:
        if ftype == "feedback_scores":
            return OQLIssue(
                "bad_value",
                f"'{field}' needs a score name: write {field}.<name>, e.g. {field}.accuracy < 0.5.",
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
    allowed = ENUM_VALUES.get(entity_type, {}).get(field)
    if allowed is not None:
        # ``in``/``not_in`` values arrive comma-joined; one bad element fails
        # the whole filter server-side, so every element is checked.
        given = raw.value.split(",") if raw.operator in LIST_VALUE_OPERATORS else [raw.value]
        bad = [v for v in given if v not in allowed]
        if bad:
            return OQLIssue(
                "bad_value",
                f"Invalid value{'s' if len(bad) > 1 else ''} "
                f"{', '.join(repr(v) for v in bad)} for '{field}': "
                f"expected one of {', '.join(allowed)}.",
            )
    return None


def compile_filters(entity_type: str, query: str) -> list[dict[str, str]]:
    """Compile an OQL string into the backend's filter array.

    Returns ``[{field, operator, key, value}, …]`` ready to be JSON-encoded into
    the ``filters`` query parameter. An empty or blank query compiles to ``[]``.
    Raises :class:`OQLError` carrying every problem found.
    """
    if entity_type not in FILTERABLE_FIELDS:
        raise OQLError(
            entity_type,
            query,
            [
                OQLIssue(
                    "unsupported_entity",
                    f"filters are not supported for {entity_type!r}. "
                    f"Filterable types: {', '.join(SUPPORTED_ENTITIES)}.",
                )
            ],
        )

    parser = _Parser(query)
    issues: list[OQLIssue] = []
    compiled: list[dict[str, str]] = []
    try:
        raw_clauses = parser.parse()
    except _SyntaxError as e:
        # Clauses parsed before the syntax error still get validated so the
        # agent sees every problem in one round.
        raw_clauses = []
        for raw in _clauses_before_error(parser):
            clause, issue = _validate(entity_type, raw)
            if issue is not None:
                issues.append(issue)
        issues.append(
            OQLIssue("syntax", f"Syntax error at position {e.position}: {e.message}", e.position)
        )
        raise OQLError(entity_type, query, issues) from None

    for raw in raw_clauses:
        clause, issue = _validate(entity_type, raw)
        if issue is not None:
            issues.append(issue)
        elif clause is not None:
            compiled.append(clause)
    if issues:
        raise OQLError(entity_type, query, issues)
    return compiled


def _clauses_before_error(parser: _Parser) -> list[_RawClause]:
    """Clauses fully parsed before a syntax error — re-run the parser on the
    prefix up to the failing clause. Cheap (queries are short) and keeps the
    parser itself free of error-recovery state."""
    prefix = parser.q[: parser.i]
    # Walk back to the last complete AND boundary and parse that prefix.
    lowered = prefix.lower()
    cut = lowered.rfind(" and ")
    if cut < 0:
        return []
    try:
        return _Parser(prefix[:cut]).parse()
    except _SyntaxError:
        return []


def filter_fields(entity_type: str) -> dict[str, FieldType]:
    """Filterable fields and their types for one entity (schema tool, columns)."""
    return dict(FILTERABLE_FIELDS[entity_type])


def filter_field_names(entity_type: str, query: str | None) -> list[str]:
    """Sorted, de-duplicated field names a query uses — keys stripped.

    For analytics: says *which fields* agents filter on without carrying the
    values or the user-named keys. Returns ``[]`` for anything that does not
    parse; the failure itself is recorded by exception class elsewhere.
    """
    if not query:
        return []
    try:
        clauses = compile_filters(entity_type, query)
    except OQLError:
        return []
    return sorted({c["field"] for c in clauses})


def render_filters(entity_type: str, clauses: list[dict[str, str]]) -> str:
    """Render a compiled filter array back to OQL, for the applied-filters header.

    Numbers on numeric fields render bare, everything else double-quoted, so
    the echoed string is itself valid input for the next call.
    """
    fields = FILTERABLE_FIELDS.get(entity_type, {})
    parts: list[str] = []
    for c in clauses:
        field, op, key, value = c["field"], c["operator"], c.get("key", ""), c.get("value", "")
        name = f"{field}.{_quote_key(key)}" if key else field
        if op in NO_VALUE_OPERATORS:
            parts.append(f"{name} {op}")
        elif op in LIST_VALUE_OPERATORS:
            items = ", ".join(_quote(v) for v in value.split(","))
            parts.append(f"{name} {op} ({items})")
        elif fields.get(field) in ("number", "feedback_scores"):
            parts.append(f"{name} {op} {value}")
        else:
            parts.append(f"{name} {op} {_quote(value)}")
    return " AND ".join(parts)


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_key(key: str) -> str:
    return key if all(ch.isalnum() or ch == "_" for ch in key) else _quote(key)


__all__ = [
    "FILTERABLE_FIELDS",
    "GRAMMAR_LINE",
    "KEYED_TYPES",
    "MILLISECOND_FIELDS",
    "OPERATORS_BY_TYPE",
    "SOURCE_DEFAULTED_ENTITIES",
    "SUPPORTED_ENTITIES",
    "USAGE_FIELDS",
    "WINDOWED_ENTITIES",
    "FieldType",
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
    "filter_fields",
    "render_filters",
]

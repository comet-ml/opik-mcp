"""The OQL grammar behind the ``list`` tool's ``filters`` string.

The same one the Opik Python SDK's ``search_traces(filter_string=…)`` accepts,
so agents learn one query form::

    <field>[.<key>] <op> <value> [AND <field> <op> <value>]*

- Connector is ``AND`` only. ``OR`` is rejected with an explicit message.
- Values are double-quoted strings (``""`` escapes a quote) or bare numbers.
- ``is_empty`` / ``is_not_empty`` take no value.
- ``in`` / ``not_in`` take a parenthesised list of quoted strings.
- ``usage.total_tokens`` / ``usage.prompt_tokens`` / ``usage.completion_tokens``
  are flat field names, not dictionary keys.

The parser is ported from the SDK (``opik/api_objects/opik_query_language.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

NO_VALUE_OPERATORS: Final = frozenset({"is_empty", "is_not_empty"})
LIST_VALUE_OPERATORS: Final = frozenset({"in", "not_in"})
GRAMMAR_LINE: Final = (
    "<field>[.<key>] <op> <value> [AND ...] — strings in double quotes, numbers bare, "
    'is_empty/is_not_empty take no value, in/not_in take ("a", "b").'
)


@dataclass(frozen=True)
class RawClause:
    field: str
    key: str | None
    operator: str
    value: str
    """Comma-joined for in/not_in, "" for is_empty/is_not_empty."""
    quoted: bool
    """True when the value was written in double quotes (vs a bare number)."""
    field_position: int


class ParseError(Exception):
    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        self.message = message
        self.position = position


class Parser:
    """Character-cursor parser ported from the SDK, minus the semantic checks.

    Produces raw clauses; ``oql._validate`` applies the field/operator/value
    tables afterwards so semantic problems in several clauses can all be
    reported at once. Syntax problems stop the parse (there is no reliable
    way to resynchronise) and are reported with the cursor position.
    """

    def __init__(self, query: str) -> None:
        self.query = query
        self.cursor = 0

    # -- helpers --

    def _at_end(self) -> bool:
        return self.cursor >= len(self.query)

    def _skip_ws(self) -> None:
        while not self._at_end() and self.query[self.cursor].isspace():
            self.cursor += 1

    def _read_word(self) -> str:
        start = self.cursor
        while not self._at_end() and (
            self.query[self.cursor].isalnum() or self.query[self.cursor] == "_"
        ):
            self.cursor += 1
        return self.query[start : self.cursor]

    def _read_quoted(self, *, what: str) -> str:
        """Cursor is on the opening quote. Returns the unescaped content."""
        open_pos = self.cursor
        self.cursor += 1
        out: list[str] = []
        while not self._at_end():
            char = self.query[self.cursor]
            if char == '"':
                if self.cursor + 1 < len(self.query) and self.query[self.cursor + 1] == '"':
                    out.append('"')
                    self.cursor += 2
                    continue
                self.cursor += 1
                return "".join(out)
            out.append(char)
            self.cursor += 1
        raise ParseError(f"missing closing quote for {what} {self.query[open_pos:]}", open_pos)

    def _read_number(self) -> str:
        start = self.cursor
        if not self._at_end() and self.query[self.cursor] == "-":
            self.cursor += 1
        digits_start = self.cursor
        while not self._at_end() and self.query[self.cursor].isdigit():
            self.cursor += 1
        if self.cursor == digits_start:
            msg = "expected a number after '-'" if self.query[start] == "-" else "expected a number"
            raise ParseError(msg, start)
        if not self._at_end() and self.query[self.cursor] == ".":
            self.cursor += 1
            frac_start = self.cursor
            while not self._at_end() and self.query[self.cursor].isdigit():
                self.cursor += 1
            if self.cursor == frac_start:
                raise ParseError("expected digits after decimal point", frac_start)
        return self.query[start : self.cursor]

    # -- grammar --

    def _parse_field(self) -> tuple[str, str | None, int]:
        self._skip_ws()
        pos = self.cursor
        if self._at_end():
            raise ParseError("unexpected end of input, expected a field name", pos)
        field = self._read_word()
        if not field:
            raise ParseError(f"expected a field name, got {self.query[pos : pos + 12]!r}", pos)
        key: str | None = None
        if not self._at_end() and self.query[self.cursor] == ".":
            self.cursor += 1
            if not self._at_end() and self.query[self.cursor] == '"':
                key = self._read_quoted(what="key")
            else:
                key_pos = self.cursor
                key = self._read_word()
                if not key:
                    raise ParseError("expected a key after '.'", key_pos)
        return field, key, pos

    def _parse_operator(self) -> str:
        self._skip_ws()
        pos = self.cursor
        if self._at_end():
            raise ParseError("unexpected end of input, expected an operator", pos)
        char = self.query[self.cursor]
        if char == "=":
            self.cursor += 1
            return "="
        if char in "<>!":
            if self.cursor + 1 < len(self.query) and self.query[self.cursor + 1] == "=":
                self.cursor += 2
                return char + "="
            if char == "!":
                raise ParseError("expected '!=' ", pos)
            self.cursor += 1
            return char
        op = self._read_word()
        if not op:
            raise ParseError(f"expected an operator, got {self.query[pos : pos + 12]!r}", pos)
        return op

    def _parse_value(self) -> tuple[str, bool]:
        self._skip_ws()
        pos = self.cursor
        if self._at_end():
            raise ParseError("unexpected end of input, expected a value", pos)
        char = self.query[self.cursor]
        if char == '"':
            return self._read_quoted(what="value"), True
        if char.isdigit() or char == "-":
            return self._read_number(), False
        raise ParseError('expected a value in double quotes ("…") or a number', pos)

    def _parse_list_value(self) -> str:
        self._skip_ws()
        pos = self.cursor
        if self._at_end() or self.query[self.cursor] != "(":
            raise ParseError(
                'expected a list in parentheses after in/not_in, e.g. in ("a", "b"); '
                "the list must start with '('",
                pos,
            )
        self.cursor += 1
        items: list[str] = []
        while True:
            self._skip_ws()
            if self._at_end():
                raise ParseError("unterminated list, missing ')'", pos)
            if self.query[self.cursor] == ")":
                if not items:
                    raise ParseError("expected at least one item inside (...)", pos)
                self.cursor += 1
                return ",".join(items)
            if items:
                if self.query[self.cursor] != ",":
                    raise ParseError("expected ',' between list items", self.cursor)
                self.cursor += 1
                self._skip_ws()
            if self._at_end() or self.query[self.cursor] != '"':
                raise ParseError("list items must be quoted strings", self.cursor)
            items.append(self._read_quoted(what="list item"))

    def parse(self) -> list[RawClause]:
        clauses: list[RawClause] = []
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
            clauses.append(RawClause(field, key, operator, value, quoted, field_pos))

            self._skip_ws()
            if self._at_end():
                return clauses
            pos = self.cursor
            connector = self._read_word()
            if connector.lower() == "and":
                continue
            if connector.lower() == "or":
                raise ParseError(
                    "OR is not supported; use AND, or run one query per alternative", pos
                )
            raise ParseError(f"trailing characters {self.query[pos:]!r}", pos)


def clauses_before_error(parser: Parser) -> list[RawClause]:
    """Clauses fully parsed before a syntax error — re-run the parser on the
    prefix up to the failing clause. Cheap (queries are short) and keeps the
    parser itself free of error-recovery state."""
    prefix = parser.query[: parser.cursor]
    # Walk back to the last complete AND boundary and parse that prefix.
    lowered = prefix.lower()
    cut = lowered.rfind(" and ")
    if cut < 0:
        return []
    try:
        return Parser(prefix[:cut]).parse()
    except ParseError:
        return []


__all__ = [
    "GRAMMAR_LINE",
    "LIST_VALUE_OPERATORS",
    "NO_VALUE_OPERATORS",
    "ParseError",
    "Parser",
    "RawClause",
    "clauses_before_error",
]

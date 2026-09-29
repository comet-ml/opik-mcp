"""The pipe-delimited table a collection page renders as.

Output mirrors ollie-assist's format: easier for the LLM to scan than nested
JSON and lossless for the columns we care about. Whatever the table cuts — a
long value, a column it had no room for — it says so under the rows.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

from opik_mcp.read_list.columns import has_value, one_line
from opik_mcp.read_list.columns import resolve as resolve_column
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.oql import operand_values
from opik_mcp.read_list.projection import check as check_fields
from opik_mcp.read_list.projection import covers, fields_line, leaves, marker, row_fields

_TRUNCATE_AT = 60
#: The cell cap for a projected page: none. Written as a number rather than as
#: ``None`` so the one comparison in the renderer stays a comparison — a field
#: the caller named is returned whole, and a page of them is as long as the
#: caller asked for, the same bargain ``read`` makes (see ``size.py``).
_UNCUT = 10**9


# Filter fields that make no sense as a table column: bodies (never shown in a
# list), the error container (error_type carries the useful part), source (it
# is a scope, not a per-row fact) and experiment_ids (a selector naming the
# rows, which is what the id column already is — seen live as a blank column
# on every row it selected).
_NEVER_COLUMNS = frozenset(
    {
        "input",
        "output",
        "input_json",
        "output_json",
        "error_info",
        "source",
        "experiment_ids",
        # The whole payload as one string: a column of it would be every data
        # column again, and the record does not carry the field at all — the
        # cell would come back blank on every row it matched.
        "full_data",
    }
)


def _pinned(clause: dict[str, str]) -> bool:
    """Does this clause fix its field to one value?

    A column that reads the same on every row distinguishes nothing, and the
    applied-filters header above the table already states the value. Seen
    live: ``optimization_id = "…"`` repeated a 36-character id down the page,
    a tenth of its bytes. A range or a substring still earns its column — it
    explains an order or a membership the rows would not otherwise justify.
    """
    if clause["operator"] == "=":
        return True
    return clause["operator"] == "in" and len(operand_values("in", clause.get("value", ""))) == 1


def _column_of(clause: dict[str, str]) -> str:
    """The column a clause is about: ``field.key`` for a nested reference."""
    return f"{clause['field']}.{clause['key']}" if clause.get("key") else clause["field"]


def requested_columns(sort_field: str | None, clauses: list[dict[str, str]]) -> list[str]:
    """Columns the request names — the sort field first, then filter fields in
    order of first mention. Nested references keep their ``field.key`` form."""
    out: list[str] = []
    if sort_field is not None:
        out.append(sort_field)
    for c in clauses:
        col = _column_of(c)
        if c["field"] not in _NEVER_COLUMNS and col not in out:
            out.append(col)
    return out


def pinned_columns(clauses: list[dict[str, str]]) -> frozenset[str]:
    """The exact columns the request fixed to one value.

    Exact, not by root: ``feedback_scores.acc = 0.9`` pins one score, and the
    ``feedback_scores`` summary beside it still carries every other score;
    ``metadata.environment = "staging"`` says nothing about a
    ``metadata.region contains "eu"`` column on the same request.
    """
    return frozenset(_column_of(c) for c in clauses if _pinned(c))


def _projected_columns(
    handler: EntityHandler,
    content: Sequence[Mapping[str, object]],
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    """The columns of a projected page: the id, the named fields, the handle.

    The id leads because a row nobody can address again is a dead end, and the
    entity's ``list_identity_fields`` follow for the same reason one level
    down — an experiment's prompt version, say. Neither is added when no row
    on the page fills it: an empty column is a field the records lack, which
    is exactly the mistake the naming rule exists to prevent.

    An entity the backend addresses by name alone (``score_name``) leads with
    the name instead. It is not a second-best id, it is the id: a page of
    counts with nothing saying what was counted is the dead end this rule is
    about, in the one place where dropping ``id`` would have caused it.
    """
    handle = "id" if handler.list_has_id else ("name" if handler.list_has_name else None)
    lead = (handle,) if handle and any(has_value(row, handle) for row in content) else ()
    columns = [*lead]
    columns += [f for f in fields if f not in columns]
    for extra in handler.list_identity_fields:
        if extra not in columns and any(has_value(row, extra) for row in content):
            columns.append(extra)
    return tuple(columns)


def _render_cell(column: str, value: object, *, cell_limit: int) -> tuple[str, bool]:
    """One cell, and whether the width cut took anything from it.

    The cut keeps a table scannable, which is worth a lot for a long output
    or a payload nobody reads to the end. It is worth nothing for a url: a
    link cut to 60 characters still looks like an address and opens nothing,
    which is the plausible-and-wrong failure this whole feature exists to
    stop — arriving from the renderer rather than the builder. A url is not
    read, it is clicked, so its column is exempt and no other is.
    """
    text = _render(column, value)
    if column == "url" or len(text) <= cell_limit:
        return text, False
    return text[: cell_limit - 3] + "...", True


def format_table(
    entity_type: str,
    handler: EntityHandler,
    content: Sequence[Mapping[str, object]],
    total: int,
    page: int,
    size: int,
    name: str | None,
    extra_columns: list[str] | None = None,
    pinned: frozenset[str] = frozenset(),
    *,
    fields: tuple[str, ...] | None = None,
) -> str:
    """Pipe-delimited table — mirrors ollie's ``format_table``.

    ``extra_columns`` are the fields the request sorted or filtered on; they
    are appended after the entity's default columns (deduplicated) so the
    table shows why each row is present and in what order. ``pinned`` are the
    fields a clause fixed to one value; a column of those reads the same on
    every row and the header above already states it, so it is dropped
    whichever way it got in — requested, or chosen by the entity from the page.

    ``fields`` replaces all of that. When the caller named their columns, the
    entity's choice, the request's own columns and the pinning rule are all
    answers to a question nobody asked any more: the columns are the named
    fields, in the order they were named, plus the ids that open the next
    level. The cell cut goes with them — "returns exactly those fields" is
    not a 60-character prefix of them.
    """
    base = tuple(
        column
        for column, present in (("id", handler.list_has_id), ("name", handler.list_has_name))
        if present
    )
    # Columns the record does not carry, computed before anything looks at
    # the page: the projection decides on them like any other column, and the
    # renderer resolves them like any other key.
    if handler.list_row_fn is not None:
        content = [handler.list_row_fn(item) for item in content]
    available = row_fields(content)
    if "error_type" not in available and any(
        _cell(row, "error_type") is not None for row in content
    ):
        # The one column the renderer derives rather than reads, out of the
        # error container. A table that shows a column the caller cannot then
        # name would be the naming rule failing on our own field, which is
        # exactly the guess the rule exists to spare them.
        available = tuple(sorted((*available, "error_type")))
    projection = None
    dropped: tuple[str, ...] = ()
    columns: tuple[str, ...]
    if fields is not None:
        check_fields(fields, available, whole=f"list({entity_type!r}, …)")
        columns = _projected_columns(handler, content, fields)
        # No cut: the caller named these fields to read them, and a value cut
        # to sixty characters is not the field, it is a prefix of it.
        cell_limit = _UNCUT
    else:
        projection = (
            handler.list_projection_fn(content) if handler.list_projection_fn is not None else None
        )
        chosen = projection.columns if projection is not None else handler.list_extra_fields
        cell_limit = projection.cell_limit if projection is not None else _TRUNCATE_AT
        columns = (*base, *chosen)
        for col in extra_columns or ():
            # ``feedback_scores.accuracy`` beside a ``feedback_scores`` column
            # that already renders every score as ``name=value`` is the same
            # number twice, and reads like a second metric. The summary wins.
            if col not in columns and col.partition(".")[0] not in columns:
                columns = (*columns, col)
        dropped = tuple(c for c in columns if c not in base and c in pinned)
        columns = tuple(c for c in columns if c not in dropped)
    count = len(content)
    if name:
        header = (
            f"Found {total} {entity_type}s matching {name!r} "
            f"(page {page}, showing {count} of {total}):"
        )
    else:
        header = f"Found {total} {entity_type}s (page {page}, showing {count} of {total}):"

    # The column names are data too: a dataset item's columns are the keys
    # the user chose for its ``data`` map, and one with a line break split
    # the header line in two while a bare pipe left the table with a name no
    # row had a cell for.
    col_header = " | ".join(one_line(_COLUMN_LABELS.get(c, c)) for c in columns)
    rows: list[str] = []
    cut = 0
    for item in content:
        values: list[str] = []
        for col in columns:
            text, was_cut = _render_cell(col, _cell(item, col), cell_limit=cell_limit)
            cut += was_cut
            values.append(text)
        rows.append(" | ".join(values))

    lines = [header, "", col_header, *rows]
    # What the table did to the data, said under the data. A value cut to fit
    # the row would otherwise read as the whole value, and a column the page
    # had no room for would read as a key the items never had.
    notes: list[str] = []
    if fields is not None:
        # One line does both jobs the page owes the caller: it says the answer
        # was projected (spec D3) and, by naming the rest, it says what could
        # have been asked for instead — which is the ``fields:`` line an
        # unprojected page carries, spent on the half that is still news.
        notes.append(
            marker(
                kept=columns,
                # ``covers``, not ``not in``: a caller who named
                # ``feedback_scores`` gets every score in that cell, and
                # listing feedback_scores.helpfulness as omitted beside the
                # cell rendering it is the marker contradicting the table.
                omitted=tuple(
                    c for c in leaves(available) if not any(covers(col, c) for col in columns)
                ),
                whole="Drop fields= for the row as the table chooses it.",
            )
        )
    if projection is not None and projection.note:
        notes.append(projection.note)
    if dropped:
        # The projection's note promised to account for every column the page
        # had; a column the filter pinned is left out after it decided.
        names = ", ".join(dropped)
        verb = "is" if len(dropped) == 1 else "are"
        notes.append(f"{names} {verb} pinned by the filter; the header states the value.")
    if cut:
        cut_line = f"{cut} value{'s' if cut != 1 else ''} cut at {cell_limit} chars"
        if projection is not None and projection.cut_hint:
            cut_line += f"; {projection.cut_hint}"
        notes.append(f"{cut_line}.")
    if notes:
        lines.append("")
        lines.extend(notes)
    if fields is None and (offer := fields_line(available)) is not None:
        # Every page names the fields its records carry, so the caller picks
        # from a list instead of guessing a path and getting a blank column.
        # Under the table's own notes: those say what happened to this answer,
        # and this says what a different one could be.
        if not notes:
            lines.append("")
        lines.append(offer)
    if page * size < total:
        lines.append("")
        lines.append(f"Use page={page + 1} for next {size} results.")
    if handler.list_footer is not None:
        lines.append("")
        lines.append(handler.list_footer)
    return "\n".join(lines)


def _cell(item: Mapping[str, object], col: str) -> object:
    """Resolve one column of one record.

    ``error_type`` is derived from the error container when the record does
    not carry it flat: the backend's list payload has ``error_info.exception_type``.
    A feedback-score list (``[{name, value}, …]``) renders as ``name=value`` pairs.
    A dotted column (``feedback_scores.accuracy``, ``usage.total_tokens``,
    ``metadata.environment``) resolves into the nested value: a dict by key, a
    list of named entries by ``name``. Anything missing renders empty.
    """
    if col in item:
        val = item[col]
        if isinstance(val, list) and val and all(isinstance(s, dict) for s in val):
            return _score_summary(val)
        return val
    if col == "error_type":
        info = item.get("error_info")
        if isinstance(info, dict):
            return info.get("exception_type")
    return resolve_column(item, col)


def _score_summary(scores: Iterable[Mapping[str, object]]) -> str:
    return ", ".join(f"{s.get('name')}={s.get('value')}" for s in scores if "name" in s)


# Header labels that carry the unit the backend leaves implicit. The field
# keeps its backend name in filters/sort (``duration > 5000``); only the
# column heading says ``_ms`` so the agent never mistakes 82.461 for seconds.
_COLUMN_LABELS = {
    "duration": "duration_ms",
    "ttft": "ttft_ms",
    # An experiment's duration is a set of percentiles rather than one number.
    # Same reasoning, one level down: the unit is not guessable from 1260.107,
    # and the label is what rounds the cell to whole milliseconds.
    "duration.p50": "duration.p50_ms",
    "duration.p90": "duration.p90_ms",
    "duration.p99": "duration.p99_ms",
}
_ISO_WITH_FRACTION = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)$")


def _render(col: str, val: object) -> str:
    """One cell of the table: compact, and one cell.

    A name, a reason or a case's data can carry a line break or a bare pipe;
    either splits the row or adds a column to it. The escaping is applied
    here rather than at the call site so that rendering a cell and making it
    safe to put in a cell are one step — it is the same rule the comparison
    table applies, from the same place (``columns.one_line``).
    """
    return one_line(_compact(col, val))


def _compact(col: str, val: object) -> str:
    """The value itself: whole milliseconds, seconds-precision timestamps,
    plain decimals. Every page pays for every character here."""
    if val is None:
        return ""
    if isinstance(val, float) and not math.isfinite(val):
        return ""
    if col in _COLUMN_LABELS and isinstance(val, int | float) and not isinstance(val, bool):
        # Half-up, not banker's: 82.5 ms reads as 83, the way a person rounds.
        return str(Decimal(repr(val)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if isinstance(val, float):
        text = repr(val)
        if "e" in text or "E" in text:
            return format(Decimal(text), "f")
        return text
    if isinstance(val, str):
        iso = _ISO_WITH_FRACTION.match(val)
        if iso:
            offset = "Z" if iso.group(2) in ("Z", "+00:00") else iso.group(2)
            return iso.group(1) + offset
    if isinstance(val, dict | list):
        # Compact JSON, not Python's repr: a nested value is still the value,
        # readable and pasteable, rather than a hint that one was there.
        return json.dumps(val, separators=(",", ":"), default=str)
    return str(val)

"""``fields=[…]`` — the caller names what comes back.

A list row is a skeleton and a read is the whole record, and between them
there was nothing: reading one span's output cost the whole trace, eight to
ten thousand tokens for one wanted field. This is the middle. It is a filter
and only a filter — a list of paths to keep, applied to the answer before it
is serialised. No jq, no computation, no derived fields: what comes back is a
subset of what would have come back, with nothing in it the whole answer did
not already have.

Three things hold it together, and each exists because the obvious version of
this feature is a silent cut:

*Naming.* A page says which fields its records carry, so the caller picks from
a list rather than guessing a path and getting an empty column. Every name it
accepts is one :mod:`opik_mcp.read_list.columns` resolves, so the offer and
the lookup cannot disagree. The printed line is a subset — a hundred-key
``metadata`` would be the payload again — and it counts what it left out, but
the counted remainder is accepted all the same. Offering less than is accepted
is a short menu; accepting less than is offered would be a lie.

*Refusing.* A field that names nothing is an error with the valid names in it,
never an empty column. An empty column is indistinguishable from a field the
records genuinely do not fill, and the caller acts on the difference.

*Declaring.* A projected answer says it was projected and what it left out
(spec invariant D3). This is the load-bearing one: the whole point of
:mod:`opik_mcp.read_list.size` is that this server does not hand back a cut
answer the caller has no way to suspect. A projection is a cut the caller
asked for, which makes it legitimate — and only as long as the answer says so,
because the caller who asked is not always the one who reads.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

from opik_mcp.read_list.columns import one_line
from opik_mcp.read_list.errors import EntityArgValidationError


class FieldsError(EntityArgValidationError):
    """A ``fields`` entry that names nothing the records carry.

    Its own class rather than the coarse argument error because "which field
    did the agent reach for and miss" is the question this feature will be
    judged on, and the analytics wrapper records exception class names.
    """


MAX_NESTED_NAMES: Final = 12
"""How many keys of one container a page's ``fields:`` line *shows*.

A ``metadata`` map is whatever the application logged, and a line naming
ninety of its keys is not a menu, it is the payload again. This caps the
display only: :func:`row_fields` returns every name, and that complete set is
what :func:`check` validates against. Capping the two together is a bug this
review caught — a caller could see ``metadata`` offered, ask for a key that
genuinely exists past the cap, and be told the records do not carry it.
"""

NAMED_OMISSIONS: Final = 8
"""How many omitted fields the marker names before it starts counting.

The count is the part that cannot be wrong; the names are what make it
actionable. Naming all of a wide record would put the record back.
"""

MAX_OFFERED: Final = 60
"""How many valid names a refusal lists. Generous — this is the recovery
path, and a caller who cannot see the name they wanted retries blind."""

_MAX_DEPTH: Final = 2
"""How deep :func:`record_paths` enumerates. Two levels is what a composite
read needs (``trace.output``) and the floor below which projection stops
paying for itself. Deeper paths still work; they are just not advertised."""


def normalise(fields: Sequence[str] | None) -> tuple[str, ...] | None:
    """The caller's list, trimmed and deduplicated, or ``None`` for "all".

    An empty list is ``None`` and not "project to nothing": a caller who built
    the argument programmatically and found no fields to ask for wants the row
    they would have got anyway, not a table of ids.
    """
    if not fields:
        return None
    out: list[str] = []
    for raw in fields:
        name = str(raw).strip()
        if name and name not in out:
            out.append(name)
    return tuple(out) or None


# --- naming what is there --------------------------------------------------- #


def _named_entries(value: Any) -> list[str]:
    """Keys of a ``[{name, value}, …]`` list — the shape scores arrive in."""
    if not isinstance(value, list):
        return []
    names = [e["name"] for e in value if isinstance(e, dict) and isinstance(e.get("name"), str)]
    return names if len(names) == len(value) and names else []


def row_fields(rows: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Every name ``fields=`` takes for a page of records — all of them.

    Exactly the three shapes :func:`opik_mcp.read_list.columns.resolve` knows:
    a flat key, one level into a dict, and a named entry of a score-shaped
    list. Taken from the rows rather than from a declaration, because the
    entities whose fields are worth naming are the ones whose fields are the
    user's (``data.question``, ``metadata.region``) and not ours.

    Complete on purpose. What a page *shows* is :func:`offered`, which is a
    subset and says so; what it *accepts* is this. The two were once the same
    capped set, and a key past the cap resolved on the row and was refused
    anyway — a refusal contradicted by the record in the caller's hand.
    """
    names: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if key.startswith("_"):
                continue
            names.add(key)
            sub_keys = sorted(value) if isinstance(value, dict) else _named_entries(value)
            names.update(f"{key}.{sub}" for sub in sub_keys)
    return tuple(sorted(names))


def offered(available: Sequence[str]) -> list[str]:
    """As much of ``available`` as is worth printing, and a count of the rest.

    Two caps, because they guard different things. The per-container one keeps
    a hundred-key ``metadata`` from crowding ``id`` and ``start_time`` out of
    the line; the overall one keeps the line a line. Whatever is left out is
    counted, so a short menu never reads as a complete one — and everything
    left out is still accepted, because :func:`check` reads the full set.
    """
    shown: list[str] = []
    per_container: Counter[str] = Counter()
    for name in available:
        container, dot, _ = name.partition(".")
        if dot:
            per_container[container] += 1
            if per_container[container] > MAX_NESTED_NAMES:
                continue
        shown.append(name)
    left = len(available) - len(shown)
    if len(shown) > MAX_OFFERED:
        left += len(shown) - MAX_OFFERED
        shown = shown[:MAX_OFFERED]
    if left:
        shown.append(f"+{left} more")
    return shown


def record_paths(record: Mapping[str, Any], *, depth: int = _MAX_DEPTH) -> tuple[str, ...]:
    """Every path one record offers, stopping at lists.

    An array is kept whole — ``spans`` is a field, ``spans.input`` is not —
    because a read's arrays are collections of records that each carry the id
    that fetches them back, and descending into one would make the answer a
    query rather than a projection.
    """
    out: list[str] = []

    def walk(node: Mapping[str, Any], prefix: str, left: int) -> None:
        for key, value in node.items():
            if key.startswith("_"):
                continue
            path = f"{prefix}{key}"
            out.append(path)
            if left > 1 and isinstance(value, dict) and value:
                walk(value, f"{path}.", left - 1)

    walk(record, "", depth)
    return tuple(sorted(out))


def leaves(paths: Sequence[str]) -> tuple[str, ...]:
    """``paths`` without the containers whose children are also listed.

    ``trace`` beside ``trace.output`` is one field counted twice — and on a
    projected answer it is worse than that: the payload has a ``trace`` key,
    so calling it omitted reads as a contradiction of the thing in front of
    the caller. The container is still a name they can ask for; it is just not
    one of the things being counted.
    """
    prefixes = {path.rpartition(".")[0] for path in paths if "." in path}
    return tuple(path for path in paths if path not in prefixes)


# --- refusing what is not ---------------------------------------------------- #


def check(
    requested: Sequence[str],
    valid: Sequence[str],
    *,
    whole: str,
    accepts: Callable[[str], bool] | None = None,
) -> None:
    """Refuse the first fields that name nothing, with the names that do.

    ``accepts`` lets a caller admit a path it did not advertise — a read
    enumerates two levels and a deeper path that genuinely resolves is still
    the caller's to ask for. Refusing what works would be the one failure mode
    worse than not advertising it.
    """
    known = set(valid)
    unknown = [f for f in requested if f not in known and not (accepts and accepts(f))]
    if not unknown:
        return
    shown = offered(valid)
    named = one_line(", ".join(repr(f) for f in unknown))
    raise FieldsError(
        f"Unknown field{'s' if len(unknown) > 1 else ''} {named} in fields=. "
        f"These records carry: {one_line(', '.join(shown)) if shown else '(nothing)'}. "
        f"Drop fields= for the whole of {whole}."
    )


# --- doing it ----------------------------------------------------------------- #


def dig(record: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """``(found, value)`` for a dotted path, descending dicts only."""
    node: Any = record
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _plant(target: dict[str, Any], path: str, value: Any) -> None:
    head, _, rest = path.partition(".")
    if not rest:
        target[head] = value
        return
    branch = target.setdefault(head, {})
    if isinstance(branch, dict):
        _plant(branch, rest, value)


def identity_path(record: Mapping[str, Any]) -> str | None:
    """The path of the id a projected record must keep, if it has one.

    A record nobody can address again is a dead end: the caller narrowed the
    answer and lost the handle that widens it. Top-level ``id`` for a flat
    record; for a composite it is the primary block's — ``trace.id`` for the
    ``{trace, spans, …}`` a trace read returns.
    """
    if isinstance(record.get("id"), str):
        return "id"
    for key, value in record.items():
        if (
            not key.startswith("_")
            and isinstance(value, Mapping)
            and isinstance(value.get("id"), str)
        ):
            return f"{key}.id"
    return None


def project_record(
    record: Mapping[str, Any],
    fields: Sequence[str],
    *,
    whole: str,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    """The record cut to ``fields``: ``(projected, kept, omitted)``.

    ``kept`` is what the payload actually holds — the requested paths plus the
    id, which is there whether or not it was asked for — so the marker's count
    describes the answer in front of the caller rather than the request behind
    it.
    """
    paths = record_paths(record)
    check(fields, paths, whole=whole, accepts=lambda f: dig(record, f)[0])

    kept = list(fields)
    identity = identity_path(record)
    if identity is not None and not any(covers(f, identity) for f in kept):
        kept.append(identity)

    out: dict[str, Any] = {}
    for path in kept:
        found, value = dig(record, path)
        if found:
            _plant(out, path, value)
    omitted = tuple(p for p in leaves(paths) if not any(covers(f, p) for f in kept))
    return out, tuple(kept), omitted


def covers(field: str, path: str) -> bool:
    """Does keeping ``field`` bring ``path`` along? ``trace`` keeps
    ``trace.output``; ``trace.output`` does not keep ``trace.input``.

    Public because the omitted set of every projected answer is built from it.
    A marker that counted a kept container's children as omitted said so on
    the same screen as the cell rendering them — which is the one thing this
    line exists not to do.
    """
    return path == field or path.startswith(f"{field}.")


# --- saying so ------------------------------------------------------------------ #


def marker(*, kept: Sequence[str], omitted: Sequence[str], whole: str) -> str:
    """The line that keeps a projected answer from reading as a whole one.

    It states a count, which cannot be argued with, then names as much of the
    remainder as is useful, then says how to get the rest. Every projected
    answer carries it, on both tools, whether or not anything was omitted —
    "nothing was omitted" is itself the fact a reader needs.
    """
    total = len(kept) + len(omitted)
    if not omitted:
        return f"projected: all {total} fields named, nothing omitted. {whole}"
    named = list(omitted[:NAMED_OMISSIONS])
    if len(omitted) > NAMED_OMISSIONS:
        named.append(f"+{len(omitted) - NAMED_OMISSIONS} more")
    return (
        f"projected: {len(kept)} of {total} fields; omitted: {one_line(', '.join(named))}. {whole}"
    )


def fields_line(available: Sequence[str]) -> str | None:
    """What a page's records carry, under the page.

    The counterpart of the refusal: a caller who has this line never has to
    guess a path, which is the whole reason the argument is usable at all.
    Capped like a refusal, because a record with a hundred metadata keys would
    otherwise spend more on the menu than on the rows. A name whose key holds
    a newline or a pipe is rendered the way the table renders it, so the two
    agree; such a key cannot be projected, which is the same as before this
    argument existed, and better than a page that breaks apart around it.
    """
    if not available:
        return None
    # ``one_line`` for the same reason the table header applies it: a field
    # name is a key the user chose, and one carrying a newline split this line
    # in two — a page whose shape depends on what someone put in a dataset.
    return (
        f"fields: {one_line(', '.join(offered(available)))} — name any in fields=[…] "
        "to get those alone, uncut."
    )


__all__ = [
    "MAX_NESTED_NAMES",
    "MAX_OFFERED",
    "NAMED_OMISSIONS",
    "FieldsError",
    "check",
    "covers",
    "dig",
    "fields_line",
    "identity_path",
    "leaves",
    "marker",
    "normalise",
    "offered",
    "project_record",
    "record_paths",
    "row_fields",
]

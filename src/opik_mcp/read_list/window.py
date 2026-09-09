"""``since`` / ``until`` for the ``list`` tool.

Each bound is either an ISO-8601 instant with a timezone or a relative
shorthand — ``30m``, ``1h``, ``24h``, ``7d``, ``2w`` — resolved against the
server's current UTC time. Agents usually know the date but not the exact
moment, so "the last hour" is ``since="1h"`` rather than a timestamp they
would have to compute.

The bounds map to the backend's ``from_time`` / ``to_time`` query params.
Those are UUIDv7 bounds on the record id, i.e. the *creation* time, which
is index-aligned and cheap; for live traffic it agrees with ``start_time``
within seconds. An exact ``start_time`` bound is still available in OQL.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Final

from opik_mcp.read_list.errors import EntityArgValidationError

_RELATIVE: Final = re.compile(r"^(\d+)([mhdw])$")
_UNIT_SECONDS: Final = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
WINDOW_FORMS: Final = (
    'a relative span like "1h", "24h", "7d" or an ISO-8601 instant like "2026-09-08T10:00:00Z"'
)


class WindowError(EntityArgValidationError):
    """``since`` / ``until`` does not validate.

    Analytics buckets the failure by this class name (``cause_type``); the
    offending value never leaves the process.
    """


def is_relative(value: str) -> bool:
    """True for the shorthand form (``30m``, ``1h``, ``7d``, ``2w``)."""
    return _RELATIVE.match(value.strip()) is not None


def to_minute(dt: datetime) -> str:
    """``2026-08-09T11:03:11.376+00:00`` → ``2026-08-09T11:03Z`` for compact echoes."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


# The shape ``Instant.parse`` accepts: date, ``T``, hh:mm:ss, optional fraction,
# ``Z`` or an offset. ``datetime.fromisoformat`` is looser (space separator,
# hour-only time, bare date, naive time), so it is gated by this first.
_INSTANT: Final = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$")


def parse_instant(value: str) -> datetime | None:
    """Parse a strict ISO-8601 instant, the form the backend's ``Instant.parse``
    accepts. Anything the backend would 400 on is rejected here so the local
    check means something."""
    text = value.strip()
    if not _INSTANT.match(text):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def resolve_instant(param: str, value: str, *, now: datetime | None = None) -> datetime:
    """Turn a ``since``/``until`` value into an aware UTC datetime."""
    now = now or datetime.now(UTC)
    m = _RELATIVE.match(value.strip())
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        return now - timedelta(seconds=amount * _UNIT_SECONDS[unit])
    parsed = parse_instant(value)
    if parsed is None:
        raise WindowError(f"Invalid {param} {value!r}: expected {WINDOW_FORMS}.")
    return parsed


def resolve_window(
    since: str | None, until: str | None, *, now: datetime | None = None
) -> tuple[str | None, str | None]:
    """Resolve both bounds to ``…Z`` strings and check their order."""
    now = now or datetime.now(UTC)
    start = resolve_instant("since", since, now=now) if since is not None else None
    end = resolve_instant("until", until, now=now) if until is not None else None
    if start is not None and end is not None and end < start:
        raise WindowError(
            f"until ({format_instant(end)}) is before since ({format_instant(start)})."
        )
    return (
        format_instant(start) if start is not None else None,
        format_instant(end) if end is not None else None,
    )


def format_instant(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bound(value: str) -> datetime:
    """A bound this module produced, back to a datetime.

    The metric and summary windows measure spans between bounds they were
    handed as strings, and both used to re-parse them with a private helper
    apiece. Parsing what :func:`format_instant` writes belongs next to it.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def floor_to_second(dt: datetime) -> datetime:
    """UTC, sub-second parts dropped.

    Measure a span *after* flooring, never before: a relative bound resolves
    with microseconds, and 30 days minus 40 ms is not a whole number of days,
    so a span taken before flooring disagrees with the bounds that get printed.
    That cost a 30-day window its day count once already.
    """
    return dt.astimezone(UTC).replace(microsecond=0)


def second_precision(dt: datetime) -> str:
    """``format_instant`` on a floored instant — no milliseconds, ever."""
    return format_instant(floor_to_second(dt))


__all__ = [
    "WINDOW_FORMS",
    "WindowError",
    "floor_to_second",
    "format_instant",
    "is_relative",
    "parse_bound",
    "parse_instant",
    "resolve_instant",
    "resolve_window",
    "second_precision",
    "to_minute",
]

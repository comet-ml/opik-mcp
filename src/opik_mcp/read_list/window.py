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
    """``since`` / ``until`` does not validate (kind ``bad_window`` in analytics)."""

    kind: str = "bad_window"


def resolve_instant(param: str, value: str, *, now: datetime | None = None) -> str:
    """Turn a ``since``/``until`` value into a UTC ``…Z`` instant string."""
    now = now or datetime.now(UTC)
    m = _RELATIVE.match(value.strip())
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        return _format(now - timedelta(seconds=amount * _UNIT_SECONDS[unit]))
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise WindowError(f"Invalid {param} {value!r}: expected {WINDOW_FORMS}.")
    return _format(parsed)


def resolve_window(
    since: str | None, until: str | None, *, now: datetime | None = None
) -> tuple[str | None, str | None]:
    """Resolve both bounds and check their order."""
    now = now or datetime.now(UTC)
    from_time = resolve_instant("since", since, now=now) if since is not None else None
    to_time = resolve_instant("until", until, now=now) if until is not None else None
    if from_time and to_time and to_time < from_time:
        raise WindowError(f"until ({to_time}) is before since ({from_time}).")
    return from_time, to_time


def _format(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["WINDOW_FORMS", "WindowError", "resolve_instant", "resolve_window"]

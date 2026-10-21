"""One namespace per entity ``read`` and ``list`` can answer for.

A module here owns everything about its entity, and ends with the
:class:`~opik_mcp.read_list.handler.EntityHandler` that ``registry`` registers.
The root of ``read_list`` keeps only what every entity shares: the two
dispatchers, the handler contract, the filter language, the window vocabulary,
paging, slim child bodies and project scope.

An entity that needs more than one file gets a package instead of a module;
the import in the registry reads the same either way.
"""

from __future__ import annotations

from typing import Final

# Closed enum values come from opik-backend's own enums (Source, SpanType,
# TraceThreadStatus, VisibilityMode), confirmed against a live backend rather
# than read off the Java alone. Each entity declares its own; this is the one
# trace, span and thread share. It lives here rather than at the root of
# read_list because its values include an entity's name (``experiment``).
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
SOURCE_VALUES: Final = ("sdk", "experiment", "playground", "optimization", "evaluator", "unknown")

__all__ = ["SOURCE_VALUES"]

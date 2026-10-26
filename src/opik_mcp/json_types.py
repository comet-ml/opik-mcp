"""Types for JSON whose shape nobody here decides: a caller's payload, a backend body.

A leaf module, so any layer can import it. Pydantic models keep ``object`` for
their open fields instead: this recursive alias would add a ``$defs`` entry to
every input schema the host loads.
"""

from __future__ import annotations

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
JsonObject = dict[str, JsonValue]

__all__ = ["JsonObject", "JsonValue"]

"""The type of a parsed JSON document, for payloads nobody gives a schema.

A leaf module so the read side, the write side and the client can share it
without importing each other.
"""

from __future__ import annotations

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]

__all__ = ["JsonObject", "JsonValue"]

"""Typed builders the test files share."""

from __future__ import annotations

from typing import Protocol, cast

from opik_mcp.config import Settings


class _SettingsInit(Protocol):
    def __call__(self, **values: object) -> Settings: ...


# The field-by-field signature mypy synthesises for the model rejects a
# ``**overrides`` mapping of mixed values; the constructor accepts it.
_build = cast("_SettingsInit", Settings)


def make_settings(**overrides: object) -> Settings:
    """One typed way to build Settings from a mapping of overrides, in place of
    a ``Settings(**base)`` call with a type-ignore comment in each test file."""
    return _build(**overrides)

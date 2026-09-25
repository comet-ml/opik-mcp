"""Typed builders the test files share."""

from __future__ import annotations

from typing import Protocol, cast

from opik_mcp.config import Settings


class _SettingsInit(Protocol):
    def __call__(self, *, _env_file: None, **values: object) -> Settings: ...


# Settings.__init__ as pydantic-settings defines it, not the field-only
# signature mypy synthesises for a model: that one has no ``_env_file`` and
# rejects a ``**overrides`` mapping of mixed values.
_build = cast("_SettingsInit", Settings)


def make_settings(**overrides: object) -> Settings:
    """Settings from the process env plus ``overrides``, never a developer's
    ``.env`` file, so a test sees only what it or its fixtures set."""
    return _build(_env_file=None, **overrides)

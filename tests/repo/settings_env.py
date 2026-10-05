"""The env var names ``Settings`` reads, for tests that check docs against them."""

from __future__ import annotations

from pydantic import AliasChoices

from opik_mcp.config import Settings


def env_names_the_server_reads() -> set[str]:
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        names.add(field_name.upper())
        if isinstance(field.validation_alias, AliasChoices):
            names.update(
                choice.upper()
                for choice in field.validation_alias.choices
                if isinstance(choice, str)
            )
    return names

"""The declaration a feature hands the framework."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    """Everything a feature adds outside its entity types, keyed by the name it toggles on."""

    name: str
    tool_sentences: Mapping[str, str]
    instructions_paragraph: str
    skills: Mapping[str, Callable[[], str]]

"""Parser for ``opik://`` URIs accepted as ``id`` input to the ``read`` tool.

Ollie does not implement this — ADR 0004 D1 "Mitigation" calls it out as
a forward-compat affordance so any client still surfacing the old
``opik://`` URIs to its LLM (or a user pasting one in) keeps working.

Recognized shapes (matching the deleted ``resources.py`` URI templates):

- ``opik://projects/{id}``                  → ("project", id)
- ``opik://traces/{id}``                    → ("trace", id)
- ``opik://spans/{id}``                     → ("span", id)
- ``opik://test-suites/{id}``               → ("test_suite", id)
- ``opik://experiments/{id}``               → ("experiment", id)
- ``opik://prompts/{id}``                   → ("prompt", id)

List-shaped URIs (``opik://projects``, ``opik://projects/{id}/traces``,
``opik://test-suites/{id}/items``) are accepted only as best-effort hints
toward the ``list`` tool — ``read`` is for singletons.
"""

from __future__ import annotations

import re
from typing import ClassVar, NamedTuple

from opik_mcp.error_kinds import ErrorKind


class InvalidURI(ValueError):
    """The string was prefixed ``opik://`` but didn't match any known shape."""

    # Read-tool callers raise this when a user-supplied identifier looks like
    # an opik:// URI but doesn't match any known shape — squarely a payload
    # validation failure, same bucket as a malformed UUID.
    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400


class ParsedURI(NamedTuple):
    entity_type: str
    entity_id: str


# Canonical singleton URI patterns. test-suites is hyphenated in the URI
# but the registry key is ``test_suite`` (snake_case) for consistency
# with every other entity.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^opik://projects/([^/?#]+)$"), "project"),
    (re.compile(r"^opik://traces/([^/?#]+)$"), "trace"),
    (re.compile(r"^opik://spans/([^/?#]+)$"), "span"),
    (re.compile(r"^opik://test-suites/([^/?#]+)$"), "test_suite"),
    (re.compile(r"^opik://experiments/([^/?#]+)$"), "experiment"),
    (re.compile(r"^opik://prompts/([^/?#]+)$"), "prompt"),
]


def looks_like_uri(s: str) -> bool:
    return s.startswith("opik://")


def parse(uri: str) -> ParsedURI:
    """Parse ``opik://...`` → (entity_type, id).

    Raises ``InvalidURI`` if the prefix matches but no pattern fits — that
    way callers can distinguish "user passed a UUID" (no prefix, no error)
    from "user passed a malformed URI" (prefix but unrecognized).
    """
    for pattern, entity_type in _PATTERNS:
        m = pattern.match(uri)
        if m is not None:
            return ParsedURI(entity_type=entity_type, entity_id=m.group(1))
    raise InvalidURI(
        f"URI {uri!r} starts with opik:// but matches no known entity shape. "
        "Expected e.g. opik://traces/<uuid> or opik://projects/<uuid>."
    )

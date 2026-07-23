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
- ``opik://projects/{pid}/threads/{tid}``   → ("thread", tid, project_id=pid)

A pasted Opik web thread link (``https://…/projects/{pid}/…?thread={tid}``) is
also recognized via ``looks_like_thread_url`` + ``parse`` so a user can drop a
URL straight from the UI.

List-shaped URIs (``opik://projects``, ``opik://projects/{id}/traces``,
``opik://test-suites/{id}/items``) are accepted only as best-effort hints
toward the ``list`` tool — ``read`` is for singletons.
"""

from __future__ import annotations

import re
from typing import ClassVar, NamedTuple
from urllib.parse import unquote

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
    project_id: str | None = None
    """Set only for threads — a thread id is unique only within a project, so
    the parser extracts the project from the URI/link and the read tool uses it
    to scope the fetch. ``None`` for every other entity (globally-unique ids)."""


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

# Threads carry a project id AND a thread id, so they need two capture groups —
# handled ahead of the single-id ``_PATTERNS`` in ``parse``.
_THREAD_URI_RE = re.compile(r"^opik://projects/([^/?#]+)/threads/([^/?#]+)$")
# A pasted Opik web thread link: /projects/<projectId>/... with ?thread=<threadId>.
_WEB_PROJECT_RE = re.compile(r"/projects/([^/?#]+)")
_WEB_THREAD_QS_RE = re.compile(r"[?&]thread=([^&#]+)")


def looks_like_uri(s: str) -> bool:
    return s.startswith("opik://")


def looks_like_thread_url(s: str) -> bool:
    """A pasted http(s) Opik thread link — has a project path and a ``thread`` qs.

    ``looks_like_uri`` only matches ``opik://``; a user usually pastes the web
    URL from the Opik UI instead. The read tool routes anything matching this
    through ``parse`` so the project + thread id are extracted; a plain http URL
    that isn't a thread link fails this check and falls through to raw-id handling.

    The gate uses the SAME regexes ``parse`` extracts with, so a match here
    guarantees extraction succeeds — a stray ``?other_thread=x`` (substring of
    ``thread=``) or a project-less path won't pass the gate only to fail parsing.
    """
    return (
        s.startswith(("http://", "https://"))
        and _WEB_PROJECT_RE.search(s) is not None
        and _WEB_THREAD_QS_RE.search(s) is not None
    )


def parse(uri: str) -> ParsedURI:
    """Parse ``opik://...`` → (entity_type, id).

    Raises ``InvalidURI`` if the prefix matches but no pattern fits — that
    way callers can distinguish "user passed a UUID" (no prefix, no error)
    from "user passed a malformed URI" (prefix but unrecognized).
    """
    # Threads first — both the canonical opik:// shape and a pasted web link
    # carry a project id alongside the thread id.
    tm = _THREAD_URI_RE.match(uri)
    if tm is not None:
        return ParsedURI(entity_type="thread", entity_id=tm.group(2), project_id=tm.group(1))
    if looks_like_thread_url(uri):
        pm = _WEB_PROJECT_RE.search(uri)
        qm = _WEB_THREAD_QS_RE.search(uri)
        if pm is not None and qm is not None:
            return ParsedURI(
                entity_type="thread",
                entity_id=unquote(qm.group(1)),
                project_id=pm.group(1),
            )

    for pattern, entity_type in _PATTERNS:
        m = pattern.match(uri)
        if m is not None:
            return ParsedURI(entity_type=entity_type, entity_id=m.group(1))
    raise InvalidURI(
        f"URI {uri!r} starts with opik:// but matches no known entity shape. "
        "Expected e.g. opik://traces/<uuid>, opik://projects/<uuid>, or "
        "opik://projects/<projectId>/threads/<threadId>."
    )

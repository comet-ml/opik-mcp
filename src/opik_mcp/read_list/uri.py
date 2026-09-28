"""Parser for ``opik://`` URIs accepted as ``id`` input to the ``read`` tool.

Ollie does not implement this — ADR 0004 D1 "Mitigation" calls it out as
a forward-compat affordance so any client still surfacing the old
``opik://`` URIs to its LLM (or a user pasting one in) keeps working.

Recognized shapes (matching the deleted ``resources.py`` URI templates):

- ``opik://projects/{id}``                  → ("project", id)
- ``opik://traces/{id}``                    → ("trace", id)
- ``opik://spans/{id}``                     → ("span", id)
- ``opik://datasets/{id}``                  → ("dataset", id)
- ``opik://experiments/{id}``               → ("experiment", id)
- ``opik://prompts/{id}``                   → ("prompt", id)
- ``opik://projects/{pid}/threads/{tid}``   → ("thread", tid, project_id=pid)
- ``opik://projects/{pid}/agent-insights-issues/{iid}``
                                            → ("agent_insights_issue", iid, project_id=pid)

Pasted Opik web links are also recognized via ``looks_like_opik_link`` +
``parse`` so a user can drop a URL straight from the UI:

- a thread link      ``https://…/projects/{pid}/…?thread={tid}``
- a Diagnostics link ``https://…/projects/{pid}/diagnostics…?issue={iid}``
- a compare link     ``https://…/experiments/{dsid}/compare?experiments=["{eid}"]``
  → ("experiment", first id), which is what the address bar holds when a
  user says "here's my experiment": a run has no page of its own.
- a trace link       ``…?tls_trace={tid}`` (the UI) or ``…?trace_id={tid}``
  (this server's own redirect, so a link we handed out is one we take back)

A link naming one trace wins over the compare view it sits on — that is the
record the user is looking at.

List-shaped URIs (``opik://projects``, ``opik://projects/{id}/traces``,
``opik://datasets/{id}/items``) are accepted only as best-effort hints
toward the ``list`` tool — ``read`` is for singletons.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import ClassVar, NamedTuple
from urllib.parse import unquote

from opik_mcp.error_kinds import ErrorKind

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_uuid(s: str) -> bool:
    """Is this string an id rather than a name?

    Lives here, beside the URI parser, because both callers are asking the
    same question about the same kind of token: ``read`` to decide whether an
    id needs a name lookup, and the filter compiler to refuse a value the
    backend would only reject with an error of its own. One regex, so the two
    cannot come to disagree about what an id looks like.
    """
    return bool(_UUID_RE.match(s))


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
    """Set for project-scoped entities (thread, agent_insights_issue) — the
    parser extracts the project from the URI/link and the read tool uses it to
    scope the fetch. ``None`` for every other entity (globally-unique ids)."""


#: What an address yields: the record's id, and its project where the address
#: carries one.
UriMatch = tuple[str, str | None]


@dataclass(frozen=True)
class UriPattern:
    """One address shape that names a record: an ``opik://`` URI or a pasted
    Opik web link."""

    match: Callable[[str], UriMatch | None]
    """The id (and project) this address names, or ``None`` when it is not
    this shape. Matching and extracting are one call, so passing the gate
    guarantees the extraction."""
    is_web_link: bool = False
    """A pasted http(s) link rather than an ``opik://`` URI."""


_SEGMENT = "([^/?#]+)"
_WEB_PROJECT_RE = re.compile(r"/projects/([^/?#]+)")


def is_web_link(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def opik_uri(path: str) -> UriPattern:
    """``opik://<path>``, where ``path`` names its segments ``{project}`` and ``{id}``.

    ``projects/{project}/threads/{id}`` matches ``opik://projects/p-1/threads/t-1``.
    """
    groups = [slot for slot in ("{project}", "{id}") if slot in path]
    groups.sort(key=path.index)
    regex = re.compile(
        "^opik://"
        + re.escape(path).replace(r"\{project\}", _SEGMENT).replace(r"\{id\}", _SEGMENT)
        + "$"
    )

    def match(uri: str) -> UriMatch | None:
        found = regex.match(uri)
        if found is None:
            return None
        values = dict(zip(groups, found.groups(), strict=True))
        return values["{id}"], values.get("{project}")

    return UriPattern(match=match)


def web_link(*query_keys: str, is_project_scoped: bool) -> UriPattern:
    """A pasted link that names its record in the query, as any of ``query_keys``.

    A project-scoped record also needs the ``/projects/<id>`` path segment the
    link was copied from, or it names nothing the read can scope.
    """
    query = re.compile(r"[?&](?:" + "|".join(map(re.escape, query_keys)) + r")=([^&#]+)")

    def match(url: str) -> UriMatch | None:
        if not is_web_link(url):
            return None
        found = query.search(url)
        if found is None:
            return None
        if not is_project_scoped:
            return unquote(found.group(1)), None
        project = _WEB_PROJECT_RE.search(url)
        if project is None:
            return None
        return unquote(found.group(1)), project.group(1)

    return UriPattern(match=match, is_web_link=True)


def looks_like_uri(s: str) -> bool:
    return s.startswith("opik://")


def looks_like_opik_link(s: str, patterns: Iterable[tuple[str, UriPattern]]) -> bool:
    """Any pasted Opik web link ``parse`` understands."""
    return any(pattern.is_web_link and pattern.match(s) is not None for _, pattern in patterns)


def parse(uri: str, patterns: Iterable[tuple[str, UriPattern]]) -> ParsedURI:
    """Parse ``opik://...`` or a pasted link → (entity_type, id).

    ``patterns`` are tried in order, each with the entity it names: a link can
    name two records (a trace open over the compare view it sits on), and the
    first pattern that matches is the record the user is looking at.

    Raises ``InvalidURI`` if the prefix matches but no pattern fits — that
    way callers can distinguish "user passed a UUID" (no prefix, no error)
    from "user passed a malformed URI" (prefix but unrecognized).
    """
    for entity_type, pattern in patterns:
        found = pattern.match(uri)
        if found is not None:
            entity_id, project_id = found
            return ParsedURI(entity_type=entity_type, entity_id=entity_id, project_id=project_id)
    raise InvalidURI(
        f"URI {uri!r} starts with opik:// but matches no known entity shape. "
        "Expected e.g. opik://traces/<uuid>, opik://projects/<uuid>, "
        "opik://projects/<projectId>/threads/<threadId>, or "
        "opik://projects/<projectId>/agent-insights-issues/<issueId>."
    )


__all__ = [
    "InvalidURI",
    "ParsedURI",
    "UriMatch",
    "UriPattern",
    "is_uuid",
    "is_web_link",
    "looks_like_opik_link",
    "looks_like_uri",
    "opik_uri",
    "parse",
    "web_link",
]

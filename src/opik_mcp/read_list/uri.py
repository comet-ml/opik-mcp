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

import json
import re
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


# Canonical singleton URI patterns.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^opik://projects/([^/?#]+)$"), "project"),
    (re.compile(r"^opik://traces/([^/?#]+)$"), "trace"),
    (re.compile(r"^opik://spans/([^/?#]+)$"), "span"),
    (re.compile(r"^opik://datasets/([^/?#]+)$"), "dataset"),
    # Legacy spelling: the entity was called test_suite before the rename, and
    # URIs handed out then still have to resolve.
    (re.compile(r"^opik://test-suites/([^/?#]+)$"), "dataset"),
    (re.compile(r"^opik://experiments/([^/?#]+)$"), "experiment"),
    (re.compile(r"^opik://prompts/([^/?#]+)$"), "prompt"),
]

# Threads carry a project id AND a thread id, so they need two capture groups —
# handled ahead of the single-id ``_PATTERNS`` in ``parse``.
_THREAD_URI_RE = re.compile(r"^opik://projects/([^/?#]+)/threads/([^/?#]+)$")
# Same for Diagnostics issues: the backend wants project_id on the detail call.
_ISSUE_URI_RE = re.compile(r"^opik://projects/([^/?#]+)/agent-insights-issues/([^/?#]+)$")
# A pasted Opik web link: /projects/<projectId>/... with ?thread=<threadId>
# (thread panel) or ?issue=<issueId> (Diagnostics page, open or resolved view).
_WEB_PROJECT_RE = re.compile(r"/projects/([^/?#]+)")
_WEB_THREAD_QS_RE = re.compile(r"[?&]thread=([^&#]+)")
_WEB_ISSUE_QS_RE = re.compile(r"[?&]issue=([^&#]+)")
# The compare route, workspace-level and project-level alike: the dataset is
# the path segment, the runs ride as a JSON array in the query string. This is
# the URL a user pastes when they say "here's my experiment" — it is what the
# address bar holds, because an experiment has no page of its own.
_WEB_COMPARE_RE = re.compile(r"/experiments/([^/?#]+)/compare")
_WEB_EXPERIMENTS_QS_RE = re.compile(r"[?&]experiments=([^&#]+)")
# One trace, named two ways: ``tls_trace`` by the UI's logs tab, ``trace_id``
# by this server's own redirect link — so a user pasting back a link we handed
# them lands on the read that produced it.
_WEB_TRACE_QS_RE = re.compile(r"[?&](?:tls_trace|trace_id)=([^&#]+)")


def looks_like_uri(s: str) -> bool:
    return s.startswith("opik://")


def looks_like_issue_url(s: str) -> bool:
    """A pasted http(s) Diagnostics link — has a project path and an ``issue`` qs.

    Same gate discipline as ``looks_like_thread_url``: the regexes here are the
    ones ``parse`` extracts with, so passing the gate guarantees extraction.
    """
    return (
        s.startswith(("http://", "https://"))
        and _WEB_PROJECT_RE.search(s) is not None
        and _WEB_ISSUE_QS_RE.search(s) is not None
    )


def _first_experiment_id(s: str) -> str | None:
    """The baseline run out of a compare link's ``experiments`` array.

    Returns ``None`` for anything that is not a non-empty JSON array of
    strings, so the gate below and the extraction in ``parse`` ask exactly the
    same question — an ``experiments=[]`` on a view opened with nothing
    selected must not pass a gate it cannot then satisfy.
    """
    qm = _WEB_EXPERIMENTS_QS_RE.search(s)
    if qm is None:
        return None
    try:
        runs = json.loads(unquote(qm.group(1)))
    except ValueError:
        return None
    if not isinstance(runs, list) or not runs:
        return None
    first = runs[0]
    return first if isinstance(first, str) and first else None


def looks_like_compare_url(s: str) -> bool:
    """A pasted compare link — the experiments path and a run to open."""
    return (
        s.startswith(("http://", "https://"))
        and _WEB_COMPARE_RE.search(s) is not None
        and _first_experiment_id(s) is not None
    )


def looks_like_trace_url(s: str) -> bool:
    """A pasted link that names one trace."""
    return s.startswith(("http://", "https://")) and _WEB_TRACE_QS_RE.search(s) is not None


def looks_like_opik_link(s: str) -> bool:
    """Any pasted Opik web link ``parse`` understands.

    A thread, a Diagnostics issue, a compare view or a single trace. Anything
    else http(s) falls through to raw-id handling, which is what keeps a URL
    from some other service out of the entity vocabulary.
    """
    return (
        looks_like_thread_url(s)
        or looks_like_issue_url(s)
        or looks_like_trace_url(s)
        or looks_like_compare_url(s)
    )


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

    # A link naming one trace beats the view it sits on: a compare URL with
    # ``tls_trace`` is a user looking at that trace, not at the comparison.
    if looks_like_trace_url(uri):
        qm = _WEB_TRACE_QS_RE.search(uri)
        if qm is not None:
            return ParsedURI(entity_type="trace", entity_id=unquote(qm.group(1)))

    # Diagnostics issues — canonical URI, then the pasted Diagnostics page link.
    im = _ISSUE_URI_RE.match(uri)
    if im is not None:
        return ParsedURI(
            entity_type="agent_insights_issue", entity_id=im.group(2), project_id=im.group(1)
        )
    if looks_like_issue_url(uri):
        pm = _WEB_PROJECT_RE.search(uri)
        qm = _WEB_ISSUE_QS_RE.search(uri)
        if pm is not None and qm is not None:
            return ParsedURI(
                entity_type="agent_insights_issue",
                entity_id=unquote(qm.group(1)),
                project_id=pm.group(1),
            )

    # The compare view, which is where an experiment is read from.
    if looks_like_compare_url(uri):
        first = _first_experiment_id(uri)
        if first is not None:
            return ParsedURI(entity_type="experiment", entity_id=first)

    for pattern, entity_type in _PATTERNS:
        m = pattern.match(uri)
        if m is not None:
            return ParsedURI(entity_type=entity_type, entity_id=m.group(1))
    raise InvalidURI(
        f"URI {uri!r} starts with opik:// but matches no known entity shape. "
        "Expected e.g. opik://traces/<uuid>, opik://projects/<uuid>, "
        "opik://projects/<projectId>/threads/<threadId>, or "
        "opik://projects/<projectId>/agent-insights-issues/<issueId>."
    )

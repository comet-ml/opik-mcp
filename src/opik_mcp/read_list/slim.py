"""Asking opik-backend for slim bodies on the children a read inlines.

Python-side compression was removed (see :mod:`opik_mcp.read_list.size`): it
cut fields after the whole payload had already been read, sent and parsed, and
the hint it left pointed at a jq tool this server does not have.

The backend's own cut is the opposite trade. ``?truncate=true`` on the trace,
span and thread *list* endpoints reads ClickHouse's materialised
``truncated_input`` / ``truncated_output`` columns instead of the raw ones, so
an oversized field never leaves the disk, and replaces base64 images with the
literal ``"[image]"``. What makes it safe is that ``GET /traces/{id}`` and
``GET /spans/{id}`` take no such parameter: every child carries the id that
fetches it back whole. That is the half ours was missing.

It does not, however, tell the caller. ``input_truncated`` exists in the SQL,
but the DAO spends it choosing between a parsed object and a string, and it
never reaches the API model. So the only trace a cut leaves is a body that
would have been JSON arriving as text — which is what :func:`was_cut` reads,
and why the notice is ours to write.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: ``ResponseFormattingConfig.truncationSize`` in opik-backend: a field this
#: long or longer comes back cut to it. Deployment-configurable there, so
#: treat a count derived from it as evidence rather than proof.
BACKEND_SLIM_THRESHOLD_CHARS = 10_001


def was_cut(value: object) -> bool:
    """True when ``value`` looks like a body the backend cut.

    A cut body arrives as a string, because ``substring`` breaks whatever JSON
    the field held. Uncut plain-text bodies are strings too, hence the length
    test. It under-reports — an image substitution can shrink a cut field back
    under the threshold — and never over-reports, which is the right way round
    for a number the caller is asked to act on.
    """
    return isinstance(value, str) and len(value) >= BACKEND_SLIM_THRESHOLD_CHARS


def count_cut(children: Iterable[Mapping[str, Any]], fields: tuple[str, ...]) -> int:
    """How many of ``children`` lost bytes in at least one of ``fields``."""
    return sum(1 for child in children if any(was_cut(child.get(f)) for f in fields))


def slim_notice(*, cut: int, total: int, noun: str, whole: str) -> str:
    """The line a composite read carries above its inlined children.

    It states what happened rather than what might have: a count the caller
    can check against the payload in front of them. The image substitution is
    named either way, because nothing in the answer distinguishes an
    ``"[image]"`` the backend put there from one the application logged.
    """
    found = (
        f"{cut} of {total} {noun}s had a field cut at {BACKEND_SLIM_THRESHOLD_CHARS:,} characters"
        if cut
        else f"no {noun} reached the {BACKEND_SLIM_THRESHOLD_CHARS:,}-character cut"
    )
    return (
        f'slim: {found}, and base64 images are replaced with "[image]" '
        f"throughout. {whole} returns one {noun} whole."
    )


__all__ = ["BACKEND_SLIM_THRESHOLD_CHARS", "count_cut", "slim_notice", "was_cut"]

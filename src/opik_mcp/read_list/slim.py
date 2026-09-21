"""Slim bodies on the children a read inlines: the backend's cut, and ours.

The backend's cut is the one this server relies on (for why it has none of
its own, see :mod:`opik_mcp.read_list.size`). ``?truncate=true`` on the trace,
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

The backend's cut is per field, which bounds no answer, so this module also
holds the one cut this server makes: :func:`drop_bodies_past`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
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


def drop_bodies_past(
    children: Sequence[Mapping[str, Any]], budget: int, fields: tuple[str, ...]
) -> tuple[list[dict[str, Any]], int]:
    """Bodies until ``budget`` is spent; children and their shape always survive.

    Returns the children and how many lost their bodies. The first child keeps
    its body whatever it costs. Every dropped body is one read away.
    """
    kept: list[dict[str, Any]] = []
    spent = 0
    dropped = 0
    spending = True
    for child in children:
        record = dict(child)
        # Once the budget is gone it stays gone, so the rest are not measured:
        # serialising a body only to discard it is the one cost this function
        # exists to avoid paying.
        if spending:
            cost = len(json.dumps(record, default=str))
            if kept and spent + cost > budget:
                spending = False
            else:
                spent += cost
        if not spending:
            for field in fields:
                record.pop(field, None)
            dropped += 1
        kept.append(record)
    return kept, dropped


def dropped_notice(*, dropped: int, total: int, noun: str, budget: int) -> str:
    """What the inline budget spent. Appended to :func:`slim_notice`."""
    return (
        f"{dropped} of {total} {noun}s past the {budget:,}-character inline budget kept "
        f"their place and lost their bodies, which the same call returns."
    )


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


__all__ = ["count_cut", "drop_bodies_past", "dropped_notice", "slim_notice"]

"""How large an answer is, said in the answer.

A read returns the record whole, and this module's whole job is to put its
size on the first line. The server does no truncation of its own, because a
cut it cannot undo is a wrong answer the caller has no way to suspect: a large
answer costs context, which they can see and narrow — a span instead of its
trace, a filter instead of a page, a window instead of all time — while a
silently short one costs a conclusion, and the report that follows says "the
MCP is wrong", not "the MCP truncated my trace". Ollie-assist, where the
read/list shape comes from, can afford to cut because it caches the whole
entity and ships a jq tool to fetch the rest; this server has neither.

The one cut a read does carry is the backend's, on the children a composite
inlines (see :mod:`opik_mcp.read_list.slim`), and it is allowed because the
whole child is always one call away.

``fields=[…]`` (see :mod:`opik_mcp.read_list.projection`) is the other way
round and does not weaken any of this: the caller names what they want, so the
narrowing is the question rather than something done to the answer. What makes
it safe is the same thing that makes the backend's cut safe — it is stated. A
projected read says so in this module's header and in a line under it, because
a caller who did not write the call is the one who has to be able to tell.
"""

from __future__ import annotations

import json
from typing import Any


def compact_json(obj: Any) -> str:
    """The payload, as the caller receives it."""
    return json.dumps(obj, default=str)


#: A read's payload is JSON, not prose: measured against a host counting for
#: real, 25,422 characters was over 10,000 tokens. Erring high is the safe way.
_CHARS_PER_TOKEN = 2.5


def estimate_tokens(text: str) -> int:
    """Rough estimate, for the header only."""
    return int(len(text) / _CHARS_PER_TOKEN)


def size_header(
    entity_type: str,
    entity_id: str,
    tokens: int,
    *,
    projected: bool = False,
    link_name: str | None = None,
    has_link: bool = False,
) -> str:
    """The line above every read.

    The size is stated so that a large answer is visible as a large answer,
    in the answer, rather than as context that quietly ran out later.

    ``projected`` is the other half of that bargain, and the reason it is on
    the header rather than only on the line below it: a small answer is
    normally good news, and a caller who skims the first line of a projected
    read would otherwise see a cheap trace where there is a fragment of one.
    Said twice on purpose — here for the skim, and under it for what went.

    ``link_name`` says the record carries a ``url`` and what to call it. The
    rule that a bare URL is never shown to a person lives in the instructions
    blob, which not every host passes to the model; the tool result always
    reaches it. The url itself is not repeated here — it is one line below,
    and a header that carried it would be the bare URL this exists to prevent.
    """
    tag = " | projected" if projected else ""
    link = ""
    if has_link or link_name:
        link = f" | open as a link named {link_name or 'Open in Opik'!r}"
    return f"[read: {entity_type} {entity_id} | {tokens:,} tok{tag}{link}]"


__all__ = ["compact_json", "estimate_tokens", "size_header"]

"""How large an answer is, said in the answer.

This replaces ``compression.py``, and the deletion is the substance.

A read used to shrink what it returned: every string over 200 characters cut
and stamped with a jq path, and a composite too wide for that reduced to a
skeleton of ids and names. It came over from ollie-assist, where it is safe:
ollie caches the whole entity for the session and ships a ``scan`` tool, so
``[TRUNCATED … use jq('.spans[0].input')]`` is an instruction the agent can
carry out. We took the truncation and left the cache and the tool behind. The
pointer named a place that does not exist, and the cut data was gone — the
only way back was a second read with a larger budget, which nothing said.

That is a bad trade for the caller. A big answer costs context, which they
can see and can narrow; a silently short one costs a wrong conclusion, which
they cannot see at all. The reports that follow do not say "the MCP truncated
my trace", they say "the MCP is wrong", and nobody connects the two.

So nothing here cuts anything. A read returns the record whole, with its
size on the first line, and narrowing stays where it belongs: ask for a span
instead of a trace, a filter instead of a page, a window instead of all time.

The one exception is the children a composite read inlines, and it is an
exception because it has the half this code lacked: the backend cuts them
(see :mod:`opik_mcp.read_list.slim`) on endpoints whose single-entity
counterparts cannot cut, so the whole value is always one call away.
"""

from __future__ import annotations

import json
from typing import Any


def compact_json(obj: Any) -> str:
    """The payload, as the caller receives it."""
    return json.dumps(obj, default=str)


def estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token.

    Crude, and only used to tell the caller what an answer cost. Nothing
    branches on it any more.
    """
    return len(text) // 4


def size_header(entity_type: str, entity_id: str, tokens: int) -> str:
    """The line above every read.

    The size is stated so that a large answer is visible as a large answer,
    in the answer, rather than as context that quietly ran out later.
    """
    return f"[read: {entity_type} {entity_id} | {tokens:,} tok]"


__all__ = ["compact_json", "estimate_tokens", "size_header"]

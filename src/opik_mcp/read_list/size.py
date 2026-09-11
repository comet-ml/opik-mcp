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
"""

from __future__ import annotations

import json
from typing import Any


def compact_json(obj: Any) -> str:
    """The payload, as the caller receives it."""
    return json.dumps(obj, default=str)


def estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token, for the header only."""
    return len(text) // 4


def size_header(entity_type: str, entity_id: str, tokens: int) -> str:
    """The line above every read.

    The size is stated so that a large answer is visible as a large answer,
    in the answer, rather than as context that quietly ran out later.
    """
    return f"[read: {entity_type} {entity_id} | {tokens:,} tok]"


__all__ = ["compact_json", "estimate_tokens", "size_header"]

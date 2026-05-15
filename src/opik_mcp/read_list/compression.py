"""Adaptive compression for read tool output.

Ported from ollie-assist's ``tools/read/compression.py``. The same
heuristics: ~4 chars/token, FULL under 8k tokens, MEDIUM truncates long
strings with jq path hints, SKELETON is a name-only summary reserved for
very large composite reads (trace span trees today).
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

# Token thresholds — same defaults as ollie. The estimator is crude
# (len/4) but consistent across tiers, so the relative ordering is what
# matters, not the absolute numbers.
TOKEN_FULL_THRESHOLD = 8_000
TOKEN_SKELETON_THRESHOLD = 50_000
STRING_TRUNCATE_LENGTH = 200


class CompressionTier(StrEnum):
    FULL = "FULL"
    MEDIUM = "MEDIUM"
    SKELETON = "SKELETON"


def compact_json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token. Matches ollie's heuristic."""
    return len(text) // 4


def size_header(
    entity_type: str,
    entity_id: str,
    tier: CompressionTier,
    returned_tokens: int,
    full_tokens: int,
) -> str:
    return (
        f"[read: {entity_type} {entity_id} | "
        f"compression={tier} | "
        f"{returned_tokens:,} tok returned | "
        f"{full_tokens:,} tok full]"
    )


def truncate_strings(obj: Any, path: str, threshold: int = STRING_TRUNCATE_LENGTH) -> Any:
    """Recursively truncate long strings, appending jq path hints.

    Mirrors ollie's strategy — the hint tells the LLM how to re-fetch the
    full value from the session cache via a future jq tool (not in scope
    for Phase 1, but the hints remain useful as breadcrumbs).
    """
    if isinstance(obj, str):
        if len(obj) > threshold:
            cut = len(obj) - threshold
            return obj[:threshold] + f" [TRUNCATED {cut} chars — full value at {path}]"
        return obj
    if isinstance(obj, dict):
        return {k: truncate_strings(v, f"{path}.{k}", threshold) for k, v in obj.items()}
    if isinstance(obj, list):
        return [truncate_strings(item, f"{path}[{i}]", threshold) for i, item in enumerate(obj)]
    return obj


def compress(
    data: Any,
    *,
    entity_type: str,
    max_tokens: int | None = None,
) -> tuple[str, CompressionTier]:
    """Generic compression: FULL if it fits, otherwise MEDIUM with truncation.

    Composite entities (trace+spans, prompt+versions) can override by
    providing their own SKELETON renderer in the entity handler, but the
    default path covers every flat entity in the registry.
    """
    full_json = compact_json(data)
    full_tokens = estimate_tokens(full_json)

    budget = max_tokens if max_tokens is not None else TOKEN_FULL_THRESHOLD
    if full_tokens <= budget:
        return full_json, CompressionTier.FULL

    truncated = truncate_strings(data, f".{entity_type}")
    return compact_json(truncated), CompressionTier.MEDIUM

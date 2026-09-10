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


TIGHT_TRUNCATE_LENGTH = 60
"""What a string is cut to when 200 characters each still busts the budget.

Sixty is enough to recognise a payload and to see the head of an error; it is
the last stop before the skeleton, which keeps no content at all. A caller who
names a small budget is saying they want the shape more than the words, and
this hands them the shape with a sample rather than the shape alone.
"""


def fit_by_truncating(data: Any, *, budget: int) -> tuple[str, bool]:
    """The smallest truncated rendering, and whether it came in under budget.

    Two passes, not a search: the second exists because the first is often
    close. Callers that have a skeleton to fall back on use the flag to
    decide; callers that do not return what came out and let the header
    report the real size.
    """
    text = ""
    for threshold in (STRING_TRUNCATE_LENGTH, TIGHT_TRUNCATE_LENGTH):
        text = compact_json(truncate_strings(data, "", threshold))
        if estimate_tokens(text) <= budget:
            return text, True
    return text, False


SKELETON_MESSAGE_LENGTH = 200
"""How much of an error message a skeleton keeps.

Long enough for "litellm.AuthenticationError: OpenAIException - The api_key
client option must be set…" to say what to fix; short enough that a hundred
failing spans cost a few hundred tokens between them.
"""


def kept_error(record: dict[str, Any]) -> dict[str, Any]:
    """The part of ``error_info`` a skeleton keeps, or nothing.

    A skeleton is what a big composite collapses to, and a big composite is
    usually big because something retried, looped, or dumped a payload — so
    the read that lands on one is very often a read about a failure. Dropping
    ``error_info`` with the rest left the agent a list of span names and no
    reason, and the next move was to open spans one at a time hunting for the
    one that broke.

    The type and the head of the message. The traceback stays out: it is
    typically the single largest string in the record and the reason the
    budget was blown in the first place.
    """
    error = record.get("error_info")
    if not isinstance(error, dict):
        return {}
    kept = {key: error[key] for key in ("exception_type", "message") if key in error}
    message = kept.get("message")
    if isinstance(message, str) and len(message) > SKELETON_MESSAGE_LENGTH:
        kept["message"] = message[:SKELETON_MESSAGE_LENGTH] + " […]"
    return {"error_info": kept} if kept else {}


def over_budget_note(text: str, *, budget: int, asked: int | None, drill: str) -> str | None:
    """What to say when the smallest tier is still bigger than was asked for.

    There is nothing below a skeleton: it is already only ids, names and
    whatever failed. A wide trace has hundreds of those, so a caller who
    names a small budget can get an answer several times its size. Returning
    it silently would leave them to discover the overspend by counting; this
    states it and names the call that reads one piece instead of all of them.
    """
    if asked is None or estimate_tokens(text) <= budget:
        return None
    return (
        f"This is {estimate_tokens(text):,} tokens against the {asked:,} asked for: "
        f"the shape alone is bigger than the budget, and there is no smaller "
        f"rendering than ids, names and errors. {drill}"
    )


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

    # The path a hint names is relative to the payload the caller is holding,
    # so it starts empty. Seeding it with the entity type produced hints
    # nobody could use: a flat record got `.span.input` for a payload whose
    # top level *is* the span, and a composite got `.trace.trace.error_info`
    # for its own field and `.trace.spans[0]…` for a child's.
    #
    # A flat entity has no skeleton to fall back to, so this returns the
    # tightest truncation whether or not it fit. The header states the size
    # either way, and half a record beats a list of its field names.
    text, _fitted = fit_by_truncating(data, budget=budget)
    return text, CompressionTier.MEDIUM

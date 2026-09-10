"""Trace compression — each tier has to fit the budget it was given.

The ladder is FULL → MEDIUM (strings truncated, twice as hard on the second
pass) → SKELETON (the span tree, no payloads). What decides between them is
the size of the *rendered* tier against the caller's budget. It used to be
the size of the uncompressed trace against a fixed constant, which meant a
caller who named a small budget could be handed something over it while the
skeleton sat unused, and a huge trace could be reduced to bare names when
truncating would have fit and kept a sample of every payload.
"""

from __future__ import annotations

from opik_mcp.read_list.compression import CompressionTier, estimate_tokens
from opik_mcp.read_list.entities.trace import compress as _compress_trace


def test_trace_under_budget_returns_full() -> None:
    data = {
        "trace": {"id": "tr-1", "name": "small", "project_id": "p-1"},
        "spans": [{"id": "sp-1", "name": "child"}],
        "spansTruncated": False,
    }
    text, tier = _compress_trace(data, max_tokens=None)
    assert tier is CompressionTier.FULL
    assert "sp-1" in text


def test_a_huge_payload_is_truncated_rather_than_thrown_away() -> None:
    """A megabyte of input is one string. Cutting it to 200 characters fits
    any budget worth naming, so the answer keeps the shape *and* a sample of
    every field — the skeleton would keep only the names."""
    big_payload = "x" * 250_000
    data = {
        "trace": {"id": "tr-1", "name": "huge", "input": big_payload, "output": big_payload},
        "spans": [
            {"id": "sp-1", "name": "child", "input": big_payload},
            {"id": "sp-2", "name": "child2", "input": big_payload},
        ],
        "spansTruncated": False,
    }
    text, tier = _compress_trace(data, max_tokens=None)

    assert tier is CompressionTier.MEDIUM
    assert big_payload not in text
    assert "TRUNCATED" in text
    assert "sp-1" in text and "sp-2" in text
    assert "xxx" in text, "a sample of the payload survives, which is the point"


def test_a_trace_whose_shape_alone_busts_the_budget_falls_to_skeleton() -> None:
    """Two hundred spans cost tokens before a single payload is counted, so
    no amount of truncating fits a small budget. That is what the skeleton is
    for: the tree, and a pointer to read a span for the rest."""
    data = {
        "trace": {"id": "tr-1", "name": "wide", "input": "y" * 5_000},
        "spans": [
            {"id": f"sp-{i:03d}", "name": f"step-{i:03d}", "type": "llm", "input": "z" * 2_000}
            for i in range(200)
        ],
        "spansTruncated": False,
    }
    text, tier = _compress_trace(data, max_tokens=500)

    assert tier is CompressionTier.SKELETON
    assert "sp-000" in text and "sp-199" in text, "the tree survives"
    assert "zzz" not in text, "the payloads do not"
    assert "read('span', id)" in text


def test_the_budget_the_caller_named_is_the_one_that_decides() -> None:
    """Found by using the tool: `max_tokens=700` on a 3,785-token trace came
    back at 891. The tier was chosen by comparing the *full* size against a
    fixed 50,000, so the budget only decided whether to compress at all."""
    payload = "w" * 12_000
    data = {
        "trace": {"id": "tr-1", "name": "med", "input": payload, "output": payload},
        "spans": [{"id": "sp-1", "name": "child", "input": payload}],
        "spansTruncated": False,
    }

    generous, generous_tier = _compress_trace(data, max_tokens=8_000)
    assert generous_tier is CompressionTier.MEDIUM

    tight, tight_tier = _compress_trace(data, max_tokens=200)
    assert tight_tier is CompressionTier.MEDIUM, "tighter truncation still fits"
    assert estimate_tokens(tight) <= 200
    assert len(tight) < len(generous), "the second pass cuts harder"


def test_the_skeleton_keeps_the_error_it_was_opened_for() -> None:
    """A wide trace is over any budget before its payloads are counted, so a
    failing agent loop arrives as a skeleton — and the skeleton used to drop
    `error_info` with everything else. "Which traces are failing" is answered
    by a list; "why" is answered here, and it was the one field missing."""
    boom = {
        "exception_type": "AuthenticationError",
        "message": "OpenAIException - The api_key client option must be set",
        "traceback": "T" * 6_000,
    }
    data = {
        "trace": {"id": "tr-1", "name": "loop", "input": "x" * 3_000},
        "spans": [
            {
                "id": f"sp-{i:03d}",
                "name": f"step-{i:03d}",
                "type": "llm",
                "input": "z" * 2_000,
                **({"error_info": boom} if i == 137 else {}),
            }
            for i in range(200)
        ],
        "spansTruncated": False,
    }

    text, tier = _compress_trace(data, max_tokens=None)

    assert tier is CompressionTier.SKELETON
    assert "AuthenticationError" in text
    assert "api_key client option must be set" in text
    assert "sp-137" in text, "and which span it was"
    assert "TTTT" not in text, "the traceback is what made it too big; it stays out"


def test_a_skeleton_that_still_busts_the_budget_says_so() -> None:
    """There is nothing below a skeleton. A caller who asked for 700 tokens
    of a 200-span trace gets four thousand, so the answer states the
    overspend and names the call that reads one piece instead of all."""
    data = {
        "trace": {"id": "tr-1", "name": "loop"},
        "spans": [
            {"id": f"sp-{i:03d}", "name": f"step-{i:03d}", "type": "llm", "input": "z" * 900}
            for i in range(200)
        ],
        "spansTruncated": False,
    }

    text, tier = _compress_trace(data, max_tokens=700)

    assert tier is CompressionTier.SKELETON
    assert "against the 700 asked for" in text
    assert "read('span', id)" in text


def test_a_skeleton_inside_the_budget_says_nothing_extra() -> None:
    """The note is for the case that needs it. A default read of the same
    trace fits, and pays nothing for the explanation."""
    data = {
        "trace": {"id": "tr-1", "name": "loop"},
        "spans": [
            {"id": f"sp-{i:03d}", "name": f"step-{i:03d}", "type": "llm", "input": "z" * 900}
            for i in range(200)
        ],
        "spansTruncated": False,
    }

    text, _tier = _compress_trace(data, max_tokens=None)

    assert "asked for" not in text

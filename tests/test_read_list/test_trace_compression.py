"""Trace-specific SKELETON compression — drops payloads, keeps span tree."""

from __future__ import annotations

from opik_mcp.read_list.compression import CompressionTier
from opik_mcp.read_list.registry import _compress_trace


def test_trace_under_budget_returns_full() -> None:
    data = {
        "trace": {"id": "tr-1", "name": "small", "project_id": "p-1"},
        "spans": [{"id": "sp-1", "name": "child"}],
        "spansTruncated": False,
    }
    text, tier = _compress_trace(data, max_tokens=None)
    assert tier is CompressionTier.FULL
    assert "sp-1" in text


def test_trace_over_skeleton_threshold_returns_skeleton() -> None:
    """A very large trace payload triggers SKELETON — payloads gone, names kept."""
    big_payload = "x" * 250_000  # >>50k tokens
    data = {
        "trace": {"id": "tr-1", "name": "huge", "input": big_payload, "output": big_payload},
        "spans": [
            {"id": "sp-1", "name": "child", "input": big_payload},
            {"id": "sp-2", "name": "child2", "input": big_payload},
        ],
        "spansTruncated": False,
    }
    text, tier = _compress_trace(data, max_tokens=None)
    assert tier is CompressionTier.SKELETON
    # Payloads removed
    assert big_payload not in text
    # Tree preserved
    assert "sp-1" in text
    assert "sp-2" in text
    assert "child" in text
    # Hint included
    assert "SKELETON" in text


def test_trace_between_thresholds_returns_medium() -> None:
    payload = "y" * 50_000  # ~12.5k tokens — over FULL, under SKELETON
    data = {
        "trace": {"id": "tr-1", "name": "med", "input": payload},
        "spans": [],
        "spansTruncated": False,
    }
    text, tier = _compress_trace(data, max_tokens=None)
    assert tier is CompressionTier.MEDIUM
    assert "TRUNCATED" in text

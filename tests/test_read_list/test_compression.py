"""Compression heuristics — token estimation, tier selection, truncation."""

from __future__ import annotations

from opik_mcp.read_list.compression import (
    STRING_TRUNCATE_LENGTH,
    TOKEN_FULL_THRESHOLD,
    CompressionTier,
    compact_json,
    compress,
    estimate_tokens,
    size_header,
    truncate_strings,
)


def test_estimate_tokens_uses_four_chars_per_token() -> None:
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 0


def test_compact_json_handles_non_serializable_via_str() -> None:
    class X:
        def __str__(self) -> str:
            return "x-as-str"

    out = compact_json({"thing": X()})
    assert "x-as-str" in out


def test_size_header_matches_ollie_shape() -> None:
    header = size_header("trace", "tr-1", CompressionTier.FULL, 1234, 5678)
    assert header == ("[read: trace tr-1 | compression=FULL | 1,234 tok returned | 5,678 tok full]")


def test_truncate_strings_short_string_passes_through() -> None:
    assert truncate_strings("short", ".x") == "short"


def test_truncate_strings_long_string_keeps_prefix_and_hint() -> None:
    s = "a" * (STRING_TRUNCATE_LENGTH + 50)
    out = truncate_strings(s, ".x.payload")
    assert isinstance(out, str)
    assert out.startswith("a" * STRING_TRUNCATE_LENGTH)
    assert "TRUNCATED 50 chars" in out
    assert ".x.payload" in out


def test_truncate_strings_recurses_into_dict_and_list() -> None:
    long = "b" * (STRING_TRUNCATE_LENGTH + 1)
    out = truncate_strings({"k": [long]}, ".root")
    assert isinstance(out, dict)
    inner = out["k"][0]
    assert "TRUNCATED" in inner
    assert ".root.k[0]" in inner


def test_compress_returns_full_under_budget() -> None:
    text, tier = compress({"id": "p-1", "name": "demo"}, entity_type="project")
    assert tier is CompressionTier.FULL
    assert '"id"' in text and "p-1" in text


def test_compress_returns_medium_when_over_budget() -> None:
    big = {"id": "p-1", "blob": "x" * (TOKEN_FULL_THRESHOLD * 4 + 1000)}
    text, tier = compress(big, entity_type="project")
    assert tier is CompressionTier.MEDIUM
    assert "TRUNCATED" in text


def test_compress_respects_explicit_max_tokens() -> None:
    """A tiny budget forces MEDIUM even on small payloads."""
    text, tier = compress({"id": "p-1", "name": "demo" * 200}, entity_type="project", max_tokens=10)
    assert tier is CompressionTier.MEDIUM
    assert text  # truncation produced something

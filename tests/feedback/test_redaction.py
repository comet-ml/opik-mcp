from __future__ import annotations

import pytest

from opik_mcp.feedback.redaction import REDACTED, redact


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abcDEF123456ghiJKL", "abcDEF123456ghiJKL"),
        ("the call used api_key=hunter2hunter2", "hunter2hunter2"),
        ('config had "password": "s3cr3t-pass"', "s3cr3t-pass"),
        ("it fetched https://example.com/x?token=abc&sig=def", "sig=def"),
        ("it fetched https://admin:pw123@example.com/x", "admin:pw123"),
        ("AWS key AKIAABCDEFGHIJKLMNOP leaked", "AKIAABCDEFGHIJKLMNOP"),
        ("jwt eyJhbGciOi.eyJzdWIiOiIx.c2lnbmF0dXJl here", "eyJhbGciOi"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789 was set", "ghp_abcdefghij"),
        ("mail me at ops+mcp@example.co.uk please", "ops+mcp@example.co.uk"),
        ("trace 0199a3f2-7c41-7d55-9e2b-1f6c0a8d4b37 failed", "0199a3f2"),
        ('secret="two words here" was rejected', "words here"),
    ],
)
def test_a_secret_or_personal_value_is_removed(text: str, secret: str) -> None:
    result = redact(text, known_secrets=())

    assert secret not in result.text
    assert REDACTED in result.text
    assert result.removed_count >= 1


def test_the_words_the_team_needs_are_left_alone() -> None:
    text = (
        "write('agent_insights_job.trigger') returned validation_failed at "
        "2026-11-17T10:00:00Z; list('project_metric', breakdown='model') gave HTTP 422 "
        "for OPIK-9240 in opik-mcp 3.0.10. POST /v1/private/traces/search and "
        "https://www.comet.com/opik/api/v1/private/spans/stats timed out for "
        "gpt-4o-mini-2024-07-18 and claude-3-5-sonnet-20241022 under uvx opik-mcp@3.0.10."
    )

    result = redact(text, known_secrets=())

    assert result.text == text
    assert result.removed_count == 0

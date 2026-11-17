"""Remove secrets and personal data from a description before it leaves the process.

Best effort: a pattern list cannot know every key format. The tool description
asks the agent to leave user data out; this catches what slips through.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

REDACTED: Final = "[REDACTED]"

# A shorter value would match common words inside the text.
MIN_KNOWN_SECRET_LENGTH: Final = 8

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)
_URL_USERINFO = re.compile(r"(?i)(\bhttps?://)[^\s/@]+@")
_URL_QUERY = re.compile(r"(?i)(\bhttps?://[^\s?#]+)\?[^\s#]*")
# The name stays so the team can read which setting was involved.
_ASSIGNMENT = re.compile(
    r"(?i)\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|"
    r"passwd|pwd|authorization|cookie|credentials?)[\"']?\s*[:=]\s*)"
    r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"',;&\[][^\s\"',;&]*)"
)
_PATTERNS: Final = (
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[\w.~+/=-]{8,}"),
    re.compile(r"\b(?:sk|pk|rk)-[\w-]{16,}"),
    # The last label has letters only, so a package pin like ``opik-mcp@3.0.10`` stays.
    re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
)
# A key or token: a word of 20 or more characters with a run of 16 letters and
# digits mixed. Paths split on ``/``, and dated model names (``gpt-4o-mini-2024-07-18``)
# have no such run, so both stay.
_LONG_WORD = re.compile(r"(?<![\w+=-])[\w+=-]{20,}")
_MIXED_RUN = re.compile(r"[A-Za-z0-9]{16,}")


def _looks_like_a_token(word: str) -> bool:
    return any(
        any(c.isdigit() for c in run) and any(c.isalpha() for c in run)
        for run in _MIXED_RUN.findall(word)
    )


def _redact_tokens(text: str) -> tuple[str, int]:
    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        if not _looks_like_a_token(match.group()):
            return match.group()
        removed.append(match.group())
        return REDACTED

    return _LONG_WORD.sub(replace, text), len(removed)


@dataclass(frozen=True)
class RedactedText:
    text: str
    removed_count: int


def redact(text: str, *, known_secrets: Iterable[str]) -> RedactedText:
    removed_count = 0
    text, count = _PRIVATE_KEY.subn(REDACTED, text)
    removed_count += count
    for secret in known_secrets:
        if len(secret) >= MIN_KNOWN_SECRET_LENGTH and secret in text:
            removed_count += text.count(secret)
            text = text.replace(secret, REDACTED)
    text, count = _URL_USERINFO.subn(rf"\1{REDACTED}@", text)
    removed_count += count
    text, count = _URL_QUERY.subn(rf"\1?{REDACTED}", text)
    removed_count += count
    # Known shapes first, so ``Authorization: Bearer <token>`` loses the token
    # and not the word "Bearer".
    for pattern in _PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        removed_count += count
    text, count = _ASSIGNMENT.subn(rf"\1{REDACTED}", text)
    removed_count += count
    text, count = _redact_tokens(text)
    removed_count += count
    return RedactedText(text=text, removed_count=removed_count)

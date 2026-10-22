"""A feature's sentence in front of a tool's own description, at registration."""

from __future__ import annotations


def described(sentence: str | None, base: str | None) -> str | None:
    """``None`` leaves FastMCP's own derivation alone, which is what a workspace
    with no feature on gets: ``Tool.from_function`` uses ``description or
    fn.__doc__``, so the prefixed text is that same text with the sentence added."""
    if sentence is None:
        return base
    return f"{sentence} {base or ''}"

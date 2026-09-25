"""Assertions whose failure output is too long for one message."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest


def _lines(value: object) -> list[str]:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, indent=2, sort_keys=True, default=repr)
    )
    return text.splitlines(keepends=True)


def assert_answer_equals(
    actual: object, expected: object, *, artefact: Path, hint: str = ""
) -> None:
    """Fail with one line that names ``artefact``, where the full diff is written."""
    __tracebackhide__ = True
    if actual == expected:
        return
    diff = list(difflib.unified_diff(_lines(expected), _lines(actual), "expected", "actual"))
    changed = sum(1 for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---")))
    if not diff:
        pytest.fail(
            f"actual ({type(actual).__name__}) and expected ({type(expected).__name__}) "
            "render the same but are not equal",
            pytrace=False,
        )
    artefact.parent.mkdir(parents=True, exist_ok=True)
    artefact.write_text("".join(diff), encoding="utf-8")
    pytest.fail(
        f"actual differs from expected on {changed} lines; unified diff in {artefact}."
        + (f" {hint}" if hint else ""),
        pytrace=False,
    )

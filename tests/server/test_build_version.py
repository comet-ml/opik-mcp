"""The version hatch stamps on a build (OPIK-8487).

Dependabot runs `uv lock` in a clean container where `make version` never ran,
so the build version must resolve without `src/opik_mcp/_version.py`. These
tests go through `get_version`, the expression `[tool.hatch.version]` evaluates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts._build_version import get_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_release_version_from_the_environment_is_returned_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERSION", "1.2.3")
    assert get_version() == "1.2.3"


def test_without_a_release_version_the_pending_version_is_a_dev_prerelease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERSION", raising=False)
    pending = (REPO_ROOT / "version.txt").read_text().strip()
    assert get_version() == f"{pending}.dev0"

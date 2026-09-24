"""The version hatch stamps on a build; `[tool.hatch.version]` evaluates `get_version()`.

Reads no generated file, so `uv lock` and `uv build` work on a clean checkout
where `make version` never ran (Dependabot's container is one).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_version() -> str:
    """`$VERSION` when the release sets it, else `<version.txt>.dev0`, as `make version` does."""
    release_version = os.environ.get("VERSION")
    if release_version:
        return release_version
    pending = (REPO_ROOT / "version.txt").read_text().strip()
    return f"{pending}.dev0"

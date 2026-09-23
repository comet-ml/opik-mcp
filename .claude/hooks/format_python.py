# PostToolUse hook: format an edited Python file and sort its imports.
# Silent and never blocking; lint errors are left for `make lint`. Only imports
# are fixed: a full `--fix` deletes an import added one edit before its use.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Pinned so an edit under a skill-eval fixture with its own pyproject.toml
# never makes uv build a venv inside that fixture.
PROJECT = Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return 0
    raw = (call.get("tool_input") or {}).get("file_path")
    if not raw or not raw.endswith(".py") or not Path(raw).is_file():
        return 0
    if not Path(raw).resolve().is_relative_to(PROJECT):
        return 0
    for args in (["format"], ["check", "--select", "I", "--fix", "--quiet"]):
        subprocess.run(
            ["uv", "run", "--quiet", "--project", str(PROJECT), "ruff", *args, raw],
            capture_output=True,
            check=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

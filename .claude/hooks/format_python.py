"""PostToolUse hook: format and safe-fix an edited Python file.

Silent on success, so it costs no context. Never blocks: lint errors are left
for `make lint`. The fix step only sorts imports: a full `--fix` would delete
an import the agent added one edit before the code that uses it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return 0
    raw = (call.get("tool_input") or {}).get("file_path")
    if not raw or not raw.endswith(".py"):
        return 0
    path = Path(raw)
    if not path.is_file():
        return 0
    for args in (["format"], ["check", "--select", "I", "--fix", "--quiet"]):
        subprocess.run(
            ["uv", "run", "--quiet", "ruff", *args, str(path)],
            cwd=path.parent,
            capture_output=True,
            check=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The Claude Code hooks committed in `.claude/hooks/`.

Each hook reads the tool call as JSON on stdin, the way Claude Code sends it,
so the tests do the same. The protect hook blocks writes where a mistake is
silent: a skill under `.claude/skills/` or `.agents/skills/` ships to users
through `npx skills add`, and `_version.py` and `dist/` are generated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / ".claude" / "hooks"


def _run(hook: str, payload: Mapping[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def _edit(path: Path, tool: str = "Write") -> dict[str, object]:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {key: str(path)}}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    "relative",
    [
        ".claude/skills/dev-helper/SKILL.md",
        ".agents/skills/dev-helper/SKILL.md",
        "src/opik_mcp/_version.py",
        "dist/opik-skills/skills/opik/SKILL.md",
    ],
)
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_protect_blocks_with_a_reason(repo: Path, relative: str, tool: str) -> None:
    result = _run("protect_paths.py", _edit(repo / relative, tool))
    assert result.returncode == 2, "exit 2 is what makes Claude Code block the call"
    assert relative.split("/")[0] in result.stderr or "_version.py" in result.stderr
    assert "instead" in result.stderr, "the reason must say where the file belongs"


@pytest.mark.parametrize(
    "relative",
    [
        "src/opik_mcp/skills/opik/SKILL.md",
        "src/opik_mcp/read_list/uri.py",
        ".claude/rules/python.md",
        "docs/decisions/0001-context-budget-first.md",
    ],
)
def test_protect_allows_ordinary_paths(repo: Path, relative: str) -> None:
    result = _run("protect_paths.py", _edit(repo / relative))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


def test_protect_judges_a_worktree_by_its_own_root(repo: Path) -> None:
    """A worktree under `.claude/worktrees/` is its own repository: a normal
    source file there must not look like it sits under `.claude/`."""
    worktree = repo / ".claude" / "worktrees" / "some-branch"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n")
    assert _run("protect_paths.py", _edit(worktree / "src/opik_mcp/uri.py")).returncode == 0
    assert _run("protect_paths.py", _edit(worktree / ".claude/skills/x/SKILL.md")).returncode == 2


def test_protect_ignores_other_tools_and_bad_input(repo: Path) -> None:
    read = {"tool_name": "Read", "tool_input": {"file_path": str(repo / ".claude/skills/x.md")}}
    assert _run("protect_paths.py", read).returncode == 0
    broken = subprocess.run(
        [sys.executable, str(HOOKS / "protect_paths.py")],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert broken.returncode == 0, "a hook that crashes on odd input would block every edit"


@pytest.fixture
def scratch() -> Iterator[Path]:
    # Inside the repository so ruff finds this repo's config; `.tmp` is ignored.
    path = REPO_ROOT / ".tmp" / "hook-tests"
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
    if not any(path.parent.iterdir()):
        path.parent.rmdir()


def test_format_rewrites_an_edited_python_file_silently(scratch: Path) -> None:
    target = scratch / "messy.py"
    # An import not used yet must survive: the next edit is the one that uses it.
    target.write_text("import sys\nimport os\nx = {  'a':1 }\n")
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    result = _run("format_python.py", payload)
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == "", "output would cost context on every edit"
    assert target.read_text() == 'import os\nimport sys\n\nx = {"a": 1}\n'


def test_format_leaves_other_files_alone(scratch: Path) -> None:
    target = scratch / "notes.md"
    target.write_text("x = {  'a':1 }\n")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
    assert _run("format_python.py", payload).returncode == 0
    assert target.read_text() == "x = {  'a':1 }\n"


def test_settings_wire_both_hooks() -> None:
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    commands = {
        event: [hook["command"] for entry in entries for hook in entry["hooks"]]
        for event, entries in settings["hooks"].items()
    }
    assert any("protect_paths.py" in c for c in commands["PreToolUse"])
    assert any("format_python.py" in c for c in commands["PostToolUse"])
    deny = settings["permissions"]["deny"]
    assert any(".env" in rule for rule in deny)
    assert any("--force" in rule for rule in deny)

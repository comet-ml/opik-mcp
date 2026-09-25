# Hooks read the tool call as JSON on stdin, the way Claude Code sends it.

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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
@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit"])
def test_protect_blocks_with_a_reason(repo: Path, relative: str, tool: str) -> None:
    result = _run("protect_paths.py", _edit(repo / relative, tool))
    assert result.returncode == 2, "exit 2 is what makes Claude Code block the call"
    assert f"Blocked write to {relative}:" in result.stderr
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
    assert result.stdout == ""
    assert result.stderr == ""


def test_protect_judges_a_worktree_by_its_own_root(repo: Path) -> None:
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
    # A fresh folder per test, so concurrent runs don't share files.
    parent = REPO_ROOT / ".tmp"
    parent.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="hook-tests-", dir=parent))
    yield path
    shutil.rmtree(path, ignore_errors=True)
    with contextlib.suppress(OSError):
        parent.rmdir()


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
    # Output would cost context on every edit.
    assert result.stdout == ""
    assert result.stderr == ""
    # The required future import comes with the import sort.
    assert target.read_text() == (
        'from __future__ import annotations\n\nimport os\nimport sys\n\nx = {"a": 1}\n'
    )


def test_format_leaves_other_files_alone(scratch: Path) -> None:
    target = scratch / "notes.md"
    target.write_text("x = {  'a':1 }\n")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
    assert _run("format_python.py", payload).returncode == 0
    assert target.read_text() == "x = {  'a':1 }\n"


def _hook_commands(event: str) -> list[str]:
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    return [hook["command"] for entry in settings["hooks"][event] for hook in entry["hooks"]]


def _run_as_configured(command: str, payload: Mapping[str, object]) -> int:
    # The command string exactly as settings.json has it, the way Claude Code runs it.
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def test_the_configured_pre_hook_blocks_a_skill_write() -> None:
    blocked = _edit(REPO_ROOT / ".claude/skills/dev-helper/SKILL.md")
    allowed = _edit(REPO_ROOT / "src/opik_mcp/read_list/uri.py")
    commands = _hook_commands("PreToolUse")
    assert any(_run_as_configured(c, blocked) == 2 for c in commands)
    assert all(_run_as_configured(c, allowed) == 0 for c in commands)


def test_the_configured_post_hook_formats(scratch: Path) -> None:
    target = scratch / "messy.py"
    target.write_text("x = {  'a':1 }\n")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}
    for command in _hook_commands("PostToolUse"):
        _run_as_configured(command, payload)
    assert target.read_text() == 'from __future__ import annotations\n\nx = {"a": 1}\n'


def test_settings_deny_secrets_and_force_pushes() -> None:
    # A pin, not a behaviour test: Claude Code applies these rules, and a live
    # check needs a session. It stops a rule being dropped by accident.
    deny = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "Read(./.env)" in deny
    assert "Bash(git push --force*)" in deny
    assert "Bash(git push * +*)" in deny


def test_format_leaves_files_outside_the_repo_alone(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.py"
    target.write_text("x = {  'a':1 }\n")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}
    assert _run("format_python.py", payload).returncode == 0
    assert target.read_text() == "x = {  'a':1 }\n"


@pytest.mark.parametrize(
    "relative", [".CLAUDE/Skills/x/SKILL.md", "DIST/a.txt", "src/opik_mcp/_VERSION.py"]
)
def test_protect_ignores_letter_case(repo: Path, relative: str) -> None:
    assert _run("protect_paths.py", _edit(repo / relative)).returncode == 2


@pytest.mark.parametrize("hook", ["protect_paths.py", "format_python.py"])
@pytest.mark.parametrize(
    "payload",
    [
        [1],
        {"tool_name": "Write", "tool_input": []},
        {"tool_name": "Write", "tool_input": {"file_path": 5}},
    ],
)
def test_hooks_pass_odd_input_quietly(hook: str, payload: object) -> None:
    result = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""

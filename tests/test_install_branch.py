# Most tests use --dry-run, which prints the plan without running it. The plan
# gets pasted into chats and PRs, so the key must never appear in it.

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "dev" / "install_branch.py"
KEY = "sk-test-0123456789abcdef"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    (path / ".opik.config").write_text(
        f"[opik]\nurl_override = https://www.comet.com/opik/api/\n"
        f"workspace = config-ws\napi_key = {KEY}\n"
    )
    return path


def _worktree(tmp_path: Path, branch: str) -> Path:
    path = tmp_path / "wt"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True)
    return path


def _plan(
    cwd: Path, home: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OPIK_", "COMET_"))}
    clean["HOME"] = str(home)
    clean.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--dry-run"],
        cwd=cwd,
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("branch", "server"),
    [
        ("OPIK-8480-links-that-open", "opik-8480"),
        ("awkoy/OPIK-8485/agent-foundation", "opik-8485"),
        ("main", "opik-main"),
        ("feature/Some_Thing", "opik-some-thing"),
    ],
)
def test_the_server_is_named_after_the_branch(
    tmp_path: Path, home: Path, branch: str, server: str
) -> None:
    result = _plan(_worktree(tmp_path, branch), home, "install")
    assert result.returncode == 0, result.stderr
    assert f"claude mcp add -s user {server} " in result.stdout
    assert f".local/share/opik-mcp-{server.removeprefix('opik-')}" in result.stdout


def test_name_and_workspace_can_be_overridden(tmp_path: Path, home: Path) -> None:
    cwd = _worktree(tmp_path, "OPIK-1-x")
    result = _plan(cwd, home, "install", "--name", "main", "--workspace", "other-ws")
    assert " opik-main " in result.stdout
    assert "OPIK_WORKSPACE=other-ws" in result.stdout


def test_environment_wins_over_the_config_file(tmp_path: Path, home: Path) -> None:
    cwd = _worktree(tmp_path, "OPIK-1-x")
    env = {
        "OPIK_WORKSPACE": "env-ws",
        "OPIK_URL": "https://dev.comet.com/opik/api",
        "OPIK_API_KEY": "sk-env-key",
    }
    result = _plan(cwd, home, "install", env=env)
    assert result.returncode == 0, result.stderr
    assert "OPIK_WORKSPACE=env-ws" in result.stdout
    assert "OPIK_URL=https://dev.comet.com/opik/api" in result.stdout


def test_config_file_supplies_what_the_environment_does_not(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install")
    assert "OPIK_WORKSPACE=config-ws" in result.stdout
    assert "OPIK_URL=https://www.comet.com/opik/api" in result.stdout


def test_telemetry_is_always_off(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install")
    assert "OPIK_MCP_ANALYTICS_ENABLED=false" in result.stdout
    assert "OPIK_MCP_SENTRY_ENABLED=false" in result.stdout


def test_the_key_is_never_printed(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install")
    assert result.returncode == 0
    assert KEY not in result.stdout + result.stderr
    assert "OPIK_API_KEY=***" in result.stdout


def test_the_script_takes_no_key_argument(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install", "--api-key", KEY)
    assert result.returncode != 0, "a key on the command line lands in shell history"


def test_missing_credentials_fail_loudly(tmp_path: Path) -> None:
    empty = tmp_path / "empty-home"
    empty.mkdir()
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), empty, "install")
    assert result.returncode != 0
    assert "OPIK_URL" in result.stderr and "~/.opik.config" in result.stderr


def test_a_local_backend_needs_no_key(tmp_path: Path) -> None:
    empty = tmp_path / "empty-home"
    empty.mkdir()
    env = {"OPIK_URL": "http://localhost:5173/api", "OPIK_WORKSPACE": "default"}
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), empty, "install", env=env)
    assert result.returncode == 0, result.stderr
    assert "OPIK_API_KEY" not in result.stdout


def test_uninstall_removes_the_entry_and_the_venv(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-8480-x"), home, "uninstall")
    assert result.returncode == 0, result.stderr
    assert "claude mcp remove -s user opik-8480" in result.stdout
    assert "rm -rf" in result.stdout and "opik-mcp-8480" in result.stdout


def test_the_key_comes_from_the_same_source_as_the_url(tmp_path: Path, home: Path) -> None:
    env = {"OPIK_URL": "https://other.example.com/opik/api", "OPIK_WORKSPACE": "ws"}
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install", env=env)
    assert result.returncode != 0
    assert "OPIK_API_KEY" in result.stderr


def test_a_percent_sign_in_the_config_file_is_read_as_is(tmp_path: Path, home: Path) -> None:
    (home / ".opik.config").write_text(
        "[opik]\nurl_override = https://www.comet.com/opik/api/\nworkspace = ws%1\n"
        f"api_key = {KEY}\n"
    )
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install")
    assert result.returncode == 0, result.stderr
    assert "OPIK_WORKSPACE=ws%1" in result.stdout


def test_a_failed_registration_does_not_print_the_key(tmp_path: Path, home: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool, body in {
        "make": "exit 0",
        "uv": "exit 0",
        # `mcp get` and `mcp add` fail; `mcp remove` succeeds.
        "claude": '[ "$2" = remove ] && exit 0; echo "boom: $*" >&2; exit 1',
    }.items():
        script = bin_dir / tool
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPIK_", "COMET_"))}
    env |= {"HOME": str(home), "PATH": f"{bin_dir}:{env['PATH']}"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "install"],
        cwd=_worktree(tmp_path, "OPIK-1-x"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert KEY not in result.stdout + result.stderr
    assert "claude mcp add" in result.stderr, "the failure should still say which step failed"

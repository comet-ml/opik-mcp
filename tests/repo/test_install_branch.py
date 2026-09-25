# Most tests use --dry-run, which prints the plan without running it. The plan
# gets pasted into chats and PRs, so the key must never appear in it.

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    assert f"claude mcp add -s local {server} " in result.stdout
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
    assert "unrecognized arguments" in result.stderr


def test_missing_credentials_fail_loudly(tmp_path: Path) -> None:
    empty = tmp_path / "empty-home"
    empty.mkdir()
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), empty, "install")
    assert result.returncode != 0
    assert "OPIK_URL" in result.stderr
    assert "~/.opik.config" in result.stderr


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
    assert "claude mcp remove -s local opik-8480" in result.stdout
    assert "claude mcp remove -s user opik-8480" in result.stdout
    assert "rm -rf" in result.stdout
    assert "opik-mcp-8480" in result.stdout


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


def test_the_env_key_replaces_the_config_key(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("install_branch", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # dataclasses look their module up here
    spec.loader.exec_module(module)
    env = {"OPIK_URL": "https://dev.comet.com/opik/api", "OPIK_API_KEY": "sk-env-key"}
    creds = module.resolve_credentials(env, home, "ws")
    assert creds.api_key == "sk-env-key"


def test_install_is_scoped_to_this_repo(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install")
    assert "claude mcp add -s local opik-1 " in result.stdout
    assert "claude mcp add -s user" not in result.stdout
    assert "claude mcp remove -s user opik-1" in result.stdout, (
        "an old user entry would still load in every project"
    )


def test_an_env_key_is_stored_as_a_reference(tmp_path: Path, home: Path) -> None:
    env = {"OPIK_URL": "https://www.comet.com/opik/api", "OPIK_API_KEY": KEY}
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install", "--workspace", "ws", env=env)
    assert "'OPIK_API_KEY=${OPIK_API_KEY}'" in result.stdout
    assert "note:" not in result.stdout


def test_a_config_file_key_is_stored_and_says_so(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "install")
    assert "OPIK_API_KEY=***" in result.stdout
    assert "stored in ~/.claude.json" in result.stdout


def test_dogfood_config_holds_a_reference_not_the_key(tmp_path: Path, home: Path) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "dogfood-prepare")
    assert result.returncode == 0, result.stderr
    assert KEY not in result.stdout
    assert "worktree add --quiet --detach" in result.stdout
    assert "origin/main" in result.stdout
    config = json.loads(result.stdout[result.stdout.index("{") :])
    servers = config["mcpServers"]
    assert set(servers) == {"opik-branch", "opik-base"}
    for server in servers.values():
        assert server["env"]["OPIK_API_KEY"] == "${OPIK_API_KEY}"
        assert server["env"]["OPIK_MCP_ANALYTICS_ENABLED"] == "false"


def test_dogfood_run_passes_the_key_by_environment_only(tmp_path: Path, home: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv.txt"
    claude = bin_dir / "claude"
    claude.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {argv_file}\necho "env-key=$OPIK_API_KEY"\n'
    )
    claude.chmod(0o755)
    config = tmp_path / "mcp.json"
    config.write_text(_dogfood_config("https://www.comet.com/opik/api"))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("run the flows")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPIK_", "COMET_"))}
    env |= {"HOME": str(home), "PATH": f"{bin_dir}:{env['PATH']}"}
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "dogfood-run",
            "--prompt-file",
            str(prompt),
            "--config",
            str(config),
        ],
        cwd=_worktree(tmp_path, "OPIK-1-x"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "env-key=***" in result.stdout, "the child got the key, and its echo was redacted"
    argv = argv_file.read_text()
    assert KEY not in argv
    assert "--strict-mcp-config" in argv
    assert str(config) in argv
    assert "mcp__opik-branch__read" in argv
    assert "mcp__opik-base__list" in argv
    # No shell, no settings files, and write refused rather than just not approved.
    assert "--restricted" in argv
    assert "--disallowedTools\nmcp__opik-branch__write\nmcp__opik-base__write" in argv


def test_dogfood_run_dry_run_runs_nothing(tmp_path: Path, home: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("run the flows")
    result = _plan(
        _worktree(tmp_path, "OPIK-1-x"), home, "dogfood-run", "--prompt-file", str(prompt)
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("would run: claude -p")
    assert "--strict-mcp-config" in result.stdout
    assert KEY not in result.stdout


def _dogfood_config(url: str) -> str:
    env = {"OPIK_URL": url, "OPIK_API_KEY": "${OPIK_API_KEY}"}
    return json.dumps({"mcpServers": {"opik-branch": {"command": "x", "env": env}}})


@pytest.mark.parametrize("name", ["x/../../..", "../sibling", "Upper", "a b", "-x", "a" * 60])
def test_a_name_that_could_escape_its_folder_is_refused(
    tmp_path: Path, home: Path, name: str
) -> None:
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, "uninstall", "--name", name)
    assert result.returncode != 0
    assert "rm -rf" not in result.stdout


def test_dogfood_run_refuses_a_config_for_another_host(tmp_path: Path, home: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(_dogfood_config("https://elsewhere.example.com/opik/api"))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("run the flows")
    args = ("dogfood-run", "--prompt-file", str(prompt), "--config", str(config))
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), home, *args)
    assert result.returncode != 0
    assert "the key is for https://www.comet.com/opik/api" in result.stderr


def test_redaction_replaces_the_whole_key_only(tmp_path: Path) -> None:
    empty = tmp_path / "empty-home"
    empty.mkdir()
    env = {"OPIK_URL": "https://www.comet.com/opik/api", "OPIK_API_KEY": "opik"}
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), empty, "install", "--workspace", "ws", env=env)
    assert "OPIK_URL=https://www.comet.com/opik/api" in result.stdout, "a short key mangled the URL"


def test_an_env_key_without_a_url_is_explained(tmp_path: Path) -> None:
    empty = tmp_path / "empty-home"
    empty.mkdir()
    result = _plan(_worktree(tmp_path, "OPIK-1-x"), empty, "install", env={"OPIK_API_KEY": KEY})
    assert result.returncode != 0
    assert "used only together with OPIK_URL" in result.stderr

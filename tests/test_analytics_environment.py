"""Per-detector tests for analytics/environment.py.

Each detector MUST return a value from its declared allowlist, including
under adversarial inputs (paths containing the current username, exotic
parent-process names, etc.). The module's PII contract is enforced here,
not just at the property-dict boundary.
"""

from __future__ import annotations

import pytest

from opik_mcp.analytics import environment as env


def _clear_env(monkeypatch, names: list[str]) -> None:
    for n in names:
        monkeypatch.delenv(n, raising=False)


@pytest.mark.parametrize(
    "var",
    ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "JENKINS_URL"],
)
def test_detect_ci_true_when_any_known_var_set(monkeypatch, var: str) -> None:
    _clear_env(
        monkeypatch, ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "JENKINS_URL"]
    )
    monkeypatch.setenv(var, "1")
    assert env._detect_ci() == "true"


def test_detect_ci_false_when_no_var_set(monkeypatch) -> None:
    _clear_env(
        monkeypatch, ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "JENKINS_URL"]
    )
    assert env._detect_ci() == "false"


def test_detect_codespaces_true(monkeypatch) -> None:
    monkeypatch.setenv("CODESPACES", "true")
    assert env._detect_codespaces() == "true"


def test_detect_codespaces_false(monkeypatch) -> None:
    monkeypatch.delenv("CODESPACES", raising=False)
    assert env._detect_codespaces() == "false"


def test_detect_gitpod_true(monkeypatch) -> None:
    monkeypatch.setenv("GITPOD_WORKSPACE_ID", "ws-xyz")
    assert env._detect_gitpod() == "true"


def test_detect_gitpod_false(monkeypatch) -> None:
    monkeypatch.delenv("GITPOD_WORKSPACE_ID", raising=False)
    assert env._detect_gitpod() == "false"


def test_detect_pipe_signals_returns_two_booleans(monkeypatch) -> None:
    out = env._detect_pipe_signals()
    assert set(out.keys()) == {"stdin_is_pipe", "stdout_is_pipe"}
    for v in out.values():
        assert v in {"true", "false"}


# --- container detection ------------------------------------------------- #


def test_detect_container_unknown_on_non_linux(monkeypatch) -> None:
    """macOS/Windows: /proc/1/cgroup doesn't exist; emit 'unknown' not 'false'."""
    monkeypatch.setattr(env.sys, "platform", "darwin")
    assert env._detect_container() == "unknown"


def test_detect_container_true_when_dockerenv_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env.sys, "platform", "linux")
    fake_dockerenv = tmp_path / ".dockerenv"
    fake_dockerenv.touch()
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(fake_dockerenv))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(tmp_path / "no-such-file"))
    assert env._detect_container() == "true"


def test_detect_container_true_when_cgroup_mentions_docker(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env.sys, "platform", "linux")
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("12:cpu:/docker/abc123\n")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(cgroup))
    assert env._detect_container() == "true"


def test_detect_container_true_when_cgroup_mentions_kubepods(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env.sys, "platform", "linux")
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("12:memory:/kubepods/burstable/podabc/xyz\n")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(cgroup))
    assert env._detect_container() == "true"


def test_detect_container_false_on_bare_linux(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env.sys, "platform", "linux")
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("12:cpu:/user.slice/user-1000.slice\n")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(cgroup))
    assert env._detect_container() == "false"


def test_detect_container_false_when_cgroup_unreadable(monkeypatch, tmp_path) -> None:
    """Unreadable cgroup file MUST NOT raise; emits 'false' (best-effort)."""
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", "/proc/nonexistent/cgroup-7f4a")
    assert env._detect_container() == "false"


# --- launch method ------------------------------------------------------- #


@pytest.mark.parametrize(
    "executable, argv0, expected",
    [
        # uvx ships a hashed archive path under ~/.local/share/uv/archive-v0/...
        ("/Users/alice/.local/share/uv/archive-v0/abc/bin/python", "opik-mcp", "uvx"),
        ("/root/.local/share/uv/archive-v0/xyz/bin/python", "opik-mcp", "uvx"),
        # pipx
        ("/home/bob/.local/pipx/venvs/opik-mcp/bin/python", "opik-mcp", "pipx"),
        # local venv
        ("/Users/alice/projects/opik-mcp/.venv/bin/python", "opik-mcp", "venv"),
        # system python
        ("/usr/bin/python3", "opik-mcp", "system"),
        # exotic / unknown — MUST NOT leak the raw path
        ("/opt/weird-homebrew/python-${USER}-build/bin/python", "opik-mcp", "unknown"),
    ],
)
def test_detect_launch_method(monkeypatch, executable: str, argv0: str, expected: str) -> None:
    monkeypatch.setattr(env.sys, "executable", executable)
    monkeypatch.setattr(env.sys, "argv", [argv0])
    assert env._detect_launch_method() == expected


def test_detect_launch_method_never_returns_raw_path(monkeypatch) -> None:
    """Adversarial input must bucket to 'unknown', not echo the path."""
    pii = "/home/secret-user-canary-9b2a/.weird-installer/bin/python"
    monkeypatch.setattr(env.sys, "executable", pii)
    monkeypatch.setattr(env.sys, "argv", ["opik-mcp"])
    result = env._detect_launch_method()
    assert result == "unknown"
    assert "secret-user-canary-9b2a" not in result


# --- parent process ------------------------------------------------------ #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("claude", "claude"),
        ("Claude Desktop", "claude"),
        ("cursor", "cursor"),
        ("code", "vscode"),
        ("Code Helper", "vscode"),
        ("idea", "jetbrains"),
        ("pycharm", "jetbrains"),
        ("bash", "bash"),
        ("zsh", "zsh"),
        ("python3", "python"),
        ("python3.12", "python"),
        ("node", "node"),
        ("docker-entrypoint.sh", "docker-entrypoint"),
        ("sshd", "sshd"),
        ("systemd", "systemd"),
        ("launchd", "launchd"),
        # Adversarial: homebrew wrapper that happens to embed "claude" — must
        # bucket to 'claude' (privacy-safe) without leaking the raw suffix.
        ("claude-mcp-wrapper-yaro", "claude"),
        ("totally-unknown-binary", "other"),
        ("", "other"),
    ],
)
def test_classify_parent_process_name(raw: str, expected: str) -> None:
    assert env._classify_parent_process_name(raw) == expected


def test_detect_parent_process_never_leaks_raw_name(monkeypatch) -> None:
    """Raw /proc/<ppid>/comm carrying a username MUST be bucketed; the raw
    canary substring MUST NOT appear in the classifier's return value."""
    canary = "claude-mcp-wrapper-leak-canary-7c4a"
    monkeypatch.setattr(env, "_read_parent_process_name", lambda: canary)
    result = env._detect_parent_process()
    # Whatever bucket we land in, the raw per-user suffix must be dropped.
    assert canary not in result
    assert result in {
        "claude",
        "cursor",
        "vscode",
        "jetbrains",
        "bash",
        "zsh",
        "fish",
        "python",
        "node",
        "sshd",
        "systemd",
        "launchd",
        "docker-entrypoint",
        "other",
    }


# --- public aggregator --------------------------------------------------- #


def test_collect_environment_fingerprint_keys_and_value_shape(monkeypatch) -> None:
    """Aggregator returns exactly the documented key set, all str-valued."""
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", "/nonexistent")
    monkeypatch.setattr(env, "_CGROUP_PATH", "/nonexistent")
    for v in (
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "BUILDKITE",
        "CIRCLECI",
        "JENKINS_URL",
        "CODESPACES",
        "GITPOD_WORKSPACE_ID",
    ):
        monkeypatch.delenv(v, raising=False)

    out = env.collect_environment_fingerprint()
    expected_keys = {
        "is_ci",
        "is_container",
        "is_codespaces",
        "is_gitpod",
        "launch_method",
        "parent_process",
        "stdin_is_pipe",
        "stdout_is_pipe",
    }
    assert set(out.keys()) == expected_keys
    for k, v in out.items():
        assert isinstance(v, str), f"{k} must be str, got {type(v)}"
    # Sanity: low-cardinality bucketed values only
    assert out["is_ci"] in {"true", "false"}
    assert out["is_container"] in {"true", "false", "unknown"}


def test_collect_environment_fingerprint_never_raises(monkeypatch) -> None:
    """If any detector raises, the aggregator MUST still return a dict.

    Same fire-and-forget contract as track_event — instrumentation must
    never crash the host.
    """

    def _boom() -> str:
        raise RuntimeError("detector blew up")

    monkeypatch.setattr(env, "_detect_parent_process", _boom)
    out = env.collect_environment_fingerprint()
    assert isinstance(out, dict)
    assert out.get("parent_process") == "unknown"  # graceful default

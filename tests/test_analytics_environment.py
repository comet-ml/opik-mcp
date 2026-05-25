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
    _clear_env(monkeypatch, ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE",
                             "CIRCLECI", "JENKINS_URL"])
    monkeypatch.setenv(var, "1")
    assert env._detect_ci() == "true"


def test_detect_ci_false_when_no_var_set(monkeypatch) -> None:
    _clear_env(monkeypatch, ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE",
                             "CIRCLECI", "JENKINS_URL"])
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

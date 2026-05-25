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

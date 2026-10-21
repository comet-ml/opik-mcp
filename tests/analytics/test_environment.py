"""Per-detector tests for analytics/environment.py.

Each detector MUST return a value from its declared allowlist, including
under adversarial inputs (paths containing the current username, exotic
parent-process names, etc.). The module's PII contract is enforced here,
not just at the property-dict boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opik_mcp.analytics import environment as env
from opik_mcp.identity.store import credential_digest


def _clear_env(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    for n in names:
        monkeypatch.delenv(n, raising=False)


@pytest.mark.parametrize(
    "var",
    ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "JENKINS_URL"],
)
def test_detect_ci_true_when_any_known_var_set(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    _clear_env(
        monkeypatch, ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "JENKINS_URL"]
    )
    monkeypatch.setenv(var, "1")
    assert env._detect_ci() == "true"


def test_detect_ci_false_when_no_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(
        monkeypatch, ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "JENKINS_URL"]
    )
    assert env._detect_ci() == "false"


def test_detect_codespaces_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODESPACES", "true")
    assert env._detect_codespaces() == "true"


def test_detect_codespaces_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODESPACES", raising=False)
    assert env._detect_codespaces() == "false"


def test_detect_gitpod_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPOD_WORKSPACE_ID", "ws-xyz")
    assert env._detect_gitpod() == "true"


def test_detect_gitpod_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITPOD_WORKSPACE_ID", raising=False)
    assert env._detect_gitpod() == "false"


def test_detect_pipe_signals_returns_two_booleans(monkeypatch: pytest.MonkeyPatch) -> None:
    out = env._detect_pipe_signals()
    assert set(out.keys()) == {"stdin_is_pipe", "stdout_is_pipe"}
    for v in out.values():
        assert v in {"true", "false"}


# --- container detection ------------------------------------------------- #


def test_detect_container_unknown_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS/Windows: /proc/1/cgroup doesn't exist; emit 'unknown' not 'false'."""
    monkeypatch.setattr(env, "_PLATFORM", "darwin")
    assert env._detect_container() == "unknown"


def test_detect_container_true_when_dockerenv_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    fake_dockerenv = tmp_path / ".dockerenv"
    fake_dockerenv.touch()
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(fake_dockerenv))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(tmp_path / "no-such-file"))
    assert env._detect_container() == "true"


def test_detect_container_true_when_cgroup_mentions_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("12:cpu:/docker/abc123\n")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(cgroup))
    assert env._detect_container() == "true"


def test_detect_container_true_when_cgroup_mentions_kubepods(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("12:memory:/kubepods/burstable/podabc/xyz\n")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(cgroup))
    assert env._detect_container() == "true"


def test_detect_container_false_on_bare_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("12:cpu:/user.slice/user-1000.slice\n")
    monkeypatch.setattr(env, "_DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(env, "_CGROUP_PATH", str(cgroup))
    assert env._detect_container() == "false"


def test_detect_container_false_when_cgroup_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unreadable cgroup file MUST NOT raise; emits 'false' (best-effort)."""
    monkeypatch.setattr(env, "_PLATFORM", "linux")
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
def test_detect_launch_method(
    monkeypatch: pytest.MonkeyPatch, executable: str, argv0: str, expected: str
) -> None:
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(sys, "argv", [argv0])
    assert env._detect_launch_method() == expected


def test_detect_launch_method_never_returns_raw_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial input must bucket to 'unknown', not echo the path."""
    pii = "/home/secret-user-canary-9b2a/.weird-installer/bin/python"
    monkeypatch.setattr(sys, "executable", pii)
    monkeypatch.setattr(sys, "argv", ["opik-mcp"])
    result = env._detect_launch_method()
    assert result == "unknown"
    assert "secret-user-canary-9b2a" not in result


# --- public aggregator --------------------------------------------------- #


def test_collect_environment_fingerprint_keys_and_value_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregator returns exactly the documented key set, all str-valued."""
    monkeypatch.setattr(env, "_PLATFORM", "linux")
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
        "host_process",
        "launcher",
        "stdin_is_pipe",
        "stdout_is_pipe",
    }
    assert set(out.keys()) == expected_keys
    for k, v in out.items():
        assert isinstance(v, str), f"{k} must be str, got {type(v)}"
    # Sanity: low-cardinality bucketed values only
    assert out["is_ci"] in {"true", "false"}
    assert out["is_container"] in {"true", "false", "unknown"}


def test_collect_environment_fingerprint_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
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


# --- Windows launch method ------------------------------------------------ #


@pytest.mark.parametrize(
    "executable, expected",
    [
        # uv's tool cache on Windows is %LOCALAPPDATA%\uv\cache, not ~/.local/share.
        (r"C:\Users\alice\AppData\Local\uv\cache\archive-v0\ab12\Scripts\python.exe", "uvx"),
        (r"C:\Users\alice\AppData\Local\pipx\pipx\venvs\opik-mcp\Scripts\python.exe", "pipx"),
        (r"C:\dev\opik-mcp\.venv\Scripts\python.exe", "venv"),
        (r"C:\Program Files\Python313\python.exe", "system"),
        (r"C:\Users\alice\AppData\Local\Programs\Python\Python313\python.exe", "system"),
        (r"C:\Users\alice\AppData\Local\Microsoft\WindowsApps\python.exe", "system"),
        # Still bucketed, never echoed.
        (r"C:\weird\custom-build-canary-4f2a\python.exe", "unknown"),
    ],
)
def test_detect_launch_method_windows_paths(
    monkeypatch: pytest.MonkeyPatch, executable: str, expected: str
) -> None:
    """Windows reported launch_method="unknown" unconditionally before the
    separator fold — every pattern in the table was POSIX-shaped. That left
    6,645 starts of the 30-day fleet dark for this field.

    This is a coverage fix, not a redefinition: the field still means "bucketed
    sys.executable", it just no longer returns a constant on one platform.
    """
    monkeypatch.setattr(env, "_PLATFORM", "win32")
    monkeypatch.setattr(sys, "executable", executable)
    assert env._detect_launch_method() == expected


def test_detect_launch_method_windows_never_echoes_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env, "_PLATFORM", "win32")
    monkeypatch.setattr(sys, "executable", r"C:\Users\secret-canary-9b2a\odd\python.exe")
    result = env._detect_launch_method()
    assert result == "unknown"
    assert "secret-canary-9b2a" not in result


def test_fingerprint_new_fields_are_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    out = env.collect_environment_fingerprint()
    assert out["launcher"] in {"uv", "none", "unknown"}
    assert isinstance(out["host_process"], str)
    assert out["host_process"]


def test_posix_launch_method_is_unaffected_by_the_windows_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows table must never reclassify a POSIX path.

    Guards the promise that fixing Windows moved no existing series: a path that
    only a Windows-only pattern would match must still report "unknown" on
    POSIX, where the frozen table alone applies.
    """
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    for windows_only in (
        "/home/alice/.cache/uv/cache/x/bin/python",
        "/home/alice/program files/python/bin/python",
        "/home/alice/windowsapps/python",
    ):
        monkeypatch.setattr(sys, "executable", windows_only)
        assert env._detect_launch_method() == "unknown", windows_only


# --- env_id: machine identity that survives a wiped HOME ------------------ #


def test_env_id_hashes_the_machine_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRIVACY: the raw machine id must never be emitted, only its digest."""
    canary = "machine-id-canary-7f3a-do-not-emit"
    monkeypatch.setattr(env, "_read_machine_id", lambda: canary)
    env._detect_env_id.cache_clear()
    digest, kind = env.env_id()
    assert kind == "machine"
    assert digest == credential_digest(canary)
    assert canary not in digest
    env._detect_env_id.cache_clear()


def test_env_id_emits_nothing_when_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent, not a placeholder.

    A sentinel would be counted as a real machine — the exact mistake the nil
    ``install_id`` makes, where every unwritable-HOME deployment merges into one
    row. "unknown" with no digest keeps the gap countable instead.
    """
    monkeypatch.setattr(env, "_read_machine_id", lambda: "")
    env._detect_env_id.cache_clear()
    digest, kind = env.env_id()
    assert (digest, kind) == ("", "unknown")
    env._detect_env_id.cache_clear()


def test_env_id_ignores_whitespace_only_machine_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "_read_machine_id", lambda: "   \n")
    env._detect_env_id.cache_clear()
    assert env.env_id() == ("", "unknown")
    env._detect_env_id.cache_clear()


def test_env_id_is_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    """One subprocess per process — this runs on the startup path on macOS."""
    calls: list[int] = []

    def _counting() -> str:
        calls.append(1)
        return "stable-machine-id"

    monkeypatch.setattr(env, "_read_machine_id", _counting)
    env._detect_env_id.cache_clear()
    first = env.env_id()
    second = env.env_id()
    assert first == second
    assert len(calls) == 1
    env._detect_env_id.cache_clear()


def test_read_machine_id_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every platform branch is best-effort; telemetry must not break a boot."""
    monkeypatch.setattr(env, "_PLATFORM", "linux")
    monkeypatch.setattr(env, "_MACHINE_ID_PATHS", ("/nonexistent/machine-id",))
    assert env._read_machine_id() == ""
    monkeypatch.setattr(env, "_PLATFORM", "sunos")  # unknown platform
    assert env._read_machine_id() == ""

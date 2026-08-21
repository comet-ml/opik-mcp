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


def test_detect_parent_process_never_leaks_raw_name(monkeypatch: pytest.MonkeyPatch) -> None:
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


# --- frozen `parent_process` ---------------------------------------------- #
#
# `parent_process` is a long-lived BI field. Its two blind spots (the uvx runner
# and Windows) are fixed ADDITIVELY via `host_process`, never in place, so every
# dashboard built on it keeps reporting the same thing.


def test_parent_process_stays_other_for_uv_launcher() -> None:
    """FROZEN: the runner must NOT be recognised by the parent classifier."""
    assert env._classify_parent_process_name("uv") == "other"
    assert env._classify_parent_process_name("/Users/alice/.local/bin/uv") == "other"


def test_parent_process_reader_stays_posix_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """FROZEN: Windows must keep reporting "other".

    `_read_process_name` gained Windows support, but routing `parent_process`
    through it would start populating a field that has only ever been "other" on
    that platform — silently changing a live series.
    """
    monkeypatch.setattr(env, "_PLATFORM", "win32")
    monkeypatch.setattr(env, "_read_process_name", lambda _pid: "Claude.exe")
    assert env._read_parent_process_name() == ""
    assert env._detect_parent_process() == "other"


# --- additive `host_process` + `launcher` --------------------------------- #
#
# `uvx` is the install path our own README recommends, and it made the MCP host
# unidentifiable: uv spawns the interpreter, so uv is our parent and the host is
# our grandparent. In the 30-day fleet window that made `Darwin | uvx | other`
# the single largest row — 32,168 starts across 157 installs, host unknown.


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("uv", "uv"),
        ("uvx", "uv"),
        ("/Users/alice/.local/bin/uv", "uv"),
        ("uv.exe", "uv"),
        (r"C:\Users\alice\AppData\Local\uv\uv.exe", "uv"),
        # NEGATIVE CASES — why launchers are matched exactly, on the basename.
        # "uv" is two characters and the fallback pass matches substrings against
        # the full command, which on macOS is an absolute path: a substring rule
        # would classify a user named "luv" and every uvicorn process as uv.
        ("uvicorn", "other"),
        ("/Users/luv/projects/app/.venv/bin/python", "python"),
        ("/opt/uvloop-bench/bin/node", "node"),
    ],
)
def test_classify_ancestor_name_launcher_is_exact_basename_match(raw: str, expected: str) -> None:
    assert env._classify_ancestor_name(raw) == expected


def test_classify_ancestor_name_keeps_full_path_for_host_match() -> None:
    """The fallback pass must match the FULL value, not the basename.

    macOS app bundles name their helper binary generically, so the identifying
    token lives in a parent directory. Basenaming before the substring pass
    would silently regress Cursor to "other".
    """
    assert (
        env._classify_ancestor_name("/Applications/Cursor.app/Contents/MacOS/Electron") == "cursor"
    )


def test_detect_host_process_walks_through_uv_to_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the new field: report who launched us, not the runner."""
    monkeypatch.setattr(env, "_read_ancestor_parent_name", lambda: "uv")
    monkeypatch.setattr(env, "_read_parent_pid", lambda _pid: 4242)
    monkeypatch.setattr(env, "_read_process_name", lambda _pid: "Claude Helper (Renderer)")
    assert env._detect_host_process() == "claude"


def test_detect_host_process_keeps_uv_when_grandparent_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows can't resolve a ppid dependency-free — degrade to the launcher.

    "uv" is still strictly more than the "other" `parent_process` reports here.
    """
    monkeypatch.setattr(env, "_read_ancestor_parent_name", lambda: "uv")
    monkeypatch.setattr(env, "_read_parent_pid", lambda _pid: None)
    assert env._detect_host_process() == "uv"


def test_detect_host_process_keeps_uv_when_grandparent_unrecognised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised ancestor is less informative than the known launcher."""
    monkeypatch.setattr(env, "_read_ancestor_parent_name", lambda: "uv")
    monkeypatch.setattr(env, "_read_parent_pid", lambda _pid: 4242)
    monkeypatch.setattr(env, "_read_process_name", lambda _pid: "totally-unknown-binary")
    assert env._detect_host_process() == "uv"


def test_detect_host_process_does_not_read_grandparent_for_real_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No extra subprocess when the parent already identifies the host."""

    def _never(_pid: int) -> int | None:
        raise AssertionError("grandparent must not be read for a non-launcher parent")

    monkeypatch.setattr(env, "_read_ancestor_parent_name", lambda: "claude")
    monkeypatch.setattr(env, "_read_parent_pid", _never)
    assert env._detect_host_process() == "claude"


def test_host_process_sees_windows_parent_where_parent_process_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The additive field is the one that fixes the Windows blind spot."""
    monkeypatch.setattr(env, "_PLATFORM", "win32")
    monkeypatch.setattr(env, "_read_process_name", lambda _pid: "Cursor.exe")
    env._read_ancestor_parent_name.cache_clear()
    assert env._detect_host_process() == "cursor"
    assert env._detect_parent_process() == "other"  # frozen field unaffected
    env._read_ancestor_parent_name.cache_clear()


@pytest.mark.parametrize(
    "parent, expected",
    [
        ("uv", "uv"),
        ("uvx", "uv"),
        ("/Users/alice/.local/bin/uv", "uv"),
        ("claude", "none"),
        ("totally-unknown-binary", "none"),
        ("", "none"),
    ],
)
def test_detect_launcher(monkeypatch: pytest.MonkeyPatch, parent: str, expected: str) -> None:
    """`launcher` keeps the uvx install path countable after `host_process`
    folds it away in favour of the host."""
    monkeypatch.setattr(env, "_read_ancestor_parent_name", lambda: parent)
    assert env._detect_launcher() == expected


def test_read_ancestor_parent_name_is_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `ps`/`tasklist` per process, not one per consumer.

    `_detect_host_process`, `_detect_launcher` and `parent_process` all read it,
    and on macOS each uncached read is a subprocess on the startup path.
    """
    calls: list[int] = []

    def _counting(pid: int) -> str:
        calls.append(pid)
        return "claude"

    monkeypatch.setattr(env, "_PLATFORM", "darwin")
    monkeypatch.setattr(env, "_read_process_name", _counting)
    env._read_ancestor_parent_name.cache_clear()
    assert env._read_ancestor_parent_name() == "claude"
    assert env._read_ancestor_parent_name() == "claude"
    assert env._read_parent_process_name() == "claude"  # shares the same cache
    assert len(calls) == 1
    env._read_ancestor_parent_name.cache_clear()


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
    assert isinstance(out["host_process"], str) and out["host_process"]


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

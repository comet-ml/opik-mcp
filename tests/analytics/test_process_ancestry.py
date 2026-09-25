"""Per-detector tests for analytics/process_ancestry.py.

Each classifier MUST return a value from its declared allowlist, including
under adversarial inputs (process names containing the current username).
"""

from __future__ import annotations

import pytest

from opik_mcp.analytics import process_ancestry as ancestry

# --- parent process ------------------------------------------------------ #


@pytest.mark.parametrize(
    ("raw", "expected"),
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
    assert ancestry._classify_parent_process_name(raw) == expected


def test_detect_parent_process_never_leaks_raw_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw /proc/<ppid>/comm carrying a username MUST be bucketed; the raw
    canary substring MUST NOT appear in the classifier's return value."""
    canary = "claude-mcp-wrapper-leak-canary-7c4a"
    monkeypatch.setattr(ancestry, "_read_parent_process_name", lambda: canary)
    result = ancestry._detect_parent_process()
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


# --- frozen `parent_process` ---------------------------------------------- #
#
# `parent_process` is a long-lived BI field. Its two blind spots (the uvx runner
# and Windows) are fixed ADDITIVELY via `host_process`, never in place, so every
# dashboard built on it keeps reporting the same thing.


def test_parent_process_stays_other_for_uv_launcher() -> None:
    """FROZEN: the runner must NOT be recognised by the parent classifier."""
    assert ancestry._classify_parent_process_name("uv") == "other"
    assert ancestry._classify_parent_process_name("/Users/alice/.local/bin/uv") == "other"


def test_parent_process_reader_stays_posix_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """FROZEN: Windows must keep reporting "other".

    `_read_process_name` gained Windows support, but routing `parent_process`
    through it would start populating a field that has only ever been "other" on
    that platform — silently changing a live series.
    """
    monkeypatch.setattr(ancestry, "_PLATFORM", "win32")
    monkeypatch.setattr(ancestry, "_read_process_name", lambda _pid: "Claude.exe")
    assert ancestry._read_parent_process_name() == ""
    assert ancestry._detect_parent_process() == "other"


# --- additive `host_process` + `launcher` --------------------------------- #
#
# `uvx` is the install path our own README recommends, and it made the MCP host
# unidentifiable: uv spawns the interpreter, so uv is our parent and the host is
# our grandparent. In the 30-day fleet window that made `Darwin | uvx | other`
# the single largest row — 32,168 starts across 157 installs, host unknown.


@pytest.mark.parametrize(
    ("raw", "expected"),
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
    assert ancestry._classify_ancestor_name(raw) == expected


def test_classify_ancestor_name_keeps_full_path_for_host_match() -> None:
    """The fallback pass must match the FULL value, not the basename.

    macOS app bundles name their helper binary generically, so the identifying
    token lives in a parent directory. Basenaming before the substring pass
    would silently regress Cursor to "other".
    """
    cursor_helper = "/Applications/Cursor.app/Contents/MacOS/Electron"
    assert ancestry._classify_ancestor_name(cursor_helper) == "cursor"


def test_detect_host_process_walks_through_uv_to_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the new field: report who launched us, not the runner."""
    monkeypatch.setattr(ancestry, "_read_ancestor_parent_name", lambda: "uv")
    monkeypatch.setattr(ancestry, "_read_parent_pid", lambda _pid: 4242)
    monkeypatch.setattr(ancestry, "_read_process_name", lambda _pid: "Claude Helper (Renderer)")
    assert ancestry._detect_host_process() == "claude"


def test_detect_host_process_keeps_uv_when_grandparent_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows can't resolve a ppid dependency-free — degrade to the launcher.

    "uv" is still strictly more than the "other" `parent_process` reports here.
    """
    monkeypatch.setattr(ancestry, "_read_ancestor_parent_name", lambda: "uv")
    monkeypatch.setattr(ancestry, "_read_parent_pid", lambda _pid: None)
    assert ancestry._detect_host_process() == "uv"


def test_detect_host_process_keeps_uv_when_grandparent_unrecognised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised ancestor is less informative than the known launcher."""
    monkeypatch.setattr(ancestry, "_read_ancestor_parent_name", lambda: "uv")
    monkeypatch.setattr(ancestry, "_read_parent_pid", lambda _pid: 4242)
    monkeypatch.setattr(ancestry, "_read_process_name", lambda _pid: "totally-unknown-binary")
    assert ancestry._detect_host_process() == "uv"


def test_detect_host_process_does_not_read_grandparent_for_real_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No extra subprocess when the parent already identifies the host."""

    def _never(_pid: int) -> int | None:
        raise AssertionError("grandparent must not be read for a non-launcher parent")

    monkeypatch.setattr(ancestry, "_read_ancestor_parent_name", lambda: "claude")
    monkeypatch.setattr(ancestry, "_read_parent_pid", _never)
    assert ancestry._detect_host_process() == "claude"


def test_host_process_sees_windows_parent_where_parent_process_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The additive field is the one that fixes the Windows blind spot."""
    monkeypatch.setattr(ancestry, "_PLATFORM", "win32")
    monkeypatch.setattr(ancestry, "_read_process_name", lambda _pid: "Cursor.exe")
    ancestry._read_ancestor_parent_name.cache_clear()
    assert ancestry._detect_host_process() == "cursor"
    assert ancestry._detect_parent_process() == "other"  # frozen field unaffected
    ancestry._read_ancestor_parent_name.cache_clear()


@pytest.mark.parametrize(
    ("parent", "expected"),
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
    monkeypatch.setattr(ancestry, "_read_ancestor_parent_name", lambda: parent)
    assert ancestry._detect_launcher() == expected


def test_read_ancestor_parent_name_is_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `ps`/`tasklist` per process, not one per consumer.

    `_detect_host_process`, `_detect_launcher` and `parent_process` all read it,
    and on macOS each uncached read is a subprocess on the startup path.
    """
    calls: list[int] = []

    def _counting(pid: int) -> str:
        calls.append(pid)
        return "claude"

    monkeypatch.setattr(ancestry, "_PLATFORM", "darwin")
    monkeypatch.setattr(ancestry, "_read_process_name", _counting)
    ancestry._read_ancestor_parent_name.cache_clear()
    assert ancestry._read_ancestor_parent_name() == "claude"
    assert ancestry._read_ancestor_parent_name() == "claude"
    assert ancestry._read_parent_process_name() == "claude"  # shares the same cache
    assert len(calls) == 1
    ancestry._read_ancestor_parent_name.cache_clear()

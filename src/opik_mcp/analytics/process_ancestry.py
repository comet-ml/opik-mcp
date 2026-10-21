"""Which process launched us: the parent, the MCP host behind a package
runner, and whether a runner sat in between.

Same privacy contract as ``environment``: every value is bucketed or dropped,
never a raw command line. See ``tests/analytics/test_environment.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache

# A plain ``str`` for the reason given at ``environment._PLATFORM``. This copy
# drives the process readers here (parent, grandparent, ppid); the one in
# ``environment`` drives container, launch-method and machine-id detection.
# A test patches the module whose detector it exercises.
_PLATFORM: str = sys.platform

# Parent-process allowlist. Substring match on the raw comm value
# (lowercased) → bucket name. Anything not matching → "other".
#
# Order: most specific first. "docker-entrypoint" before any single token
# to keep the bucket cardinality bounded.
_PARENT_PROCESS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("docker-entrypoint", "docker-entrypoint"),
    ("claude", "claude"),
    ("cursor", "cursor"),
    ("code helper", "vscode"),
    ("code", "vscode"),
    ("vscode", "vscode"),
    ("idea", "jetbrains"),
    ("pycharm", "jetbrains"),
    ("webstorm", "jetbrains"),
    ("bash", "bash"),
    ("zsh", "zsh"),
    ("fish", "fish"),
    ("python", "python"),
    ("node", "node"),
    ("sshd", "sshd"),
    ("systemd", "systemd"),
    ("launchd", "launchd"),
)


# Package runners that sit BETWEEN the MCP host and this process. When the
# immediate parent is one of these, it tells us how we were launched but hides
# who launched us — the host is our grandparent. `_detect_parent_process` walks
# one level up through them.
#
# This was the single largest blind spot in the fleet: `uvx` is the install
# method our own README recommends, and it made `Darwin | uvx | other` the
# top row of the start table (32,168 starts across 157 installs) with the host
# unidentifiable.
_LAUNCHER_BUCKETS: frozenset[str] = frozenset({"uv"})

# Exact-match (not substring) buckets, keyed on the BASENAME of the parent
# command with any ".exe" suffix stripped.
#
# Short generic tokens MUST be matched exactly. `uv` is two characters and
# `_PARENT_PROCESS_PATTERNS` matches substrings against the full command
# string, which on macOS is an absolute path — a substring rule would classify
# `/Users/luv/...` or any `uvicorn` process as the uv launcher.
_EXACT_PARENT_PATTERNS: dict[str, str] = {
    "uv": "uv",
    "uvx": "uv",
}


def _classify_parent_process_name(raw: str) -> str:
    """Map a raw /proc/<ppid>/comm (or `ps -o comm=`) value to the allowlist.

    FROZEN — feeds the long-lived ``parent_process`` BI field. Do not change what
    this returns for any input: existing dashboards and trends are built on it.
    Improvements to ancestor detection go in ``_classify_ancestor_name`` and are
    reported through the newer ``host_process`` / ``launcher`` fields instead.

    PRIVACY: the raw value never appears in the return; it's bucketed or
    dropped. Tests inject adversarial inputs containing the local username
    to assert this.
    """
    needle = (raw or "").strip().lower()
    if not needle:
        return "other"
    for pattern, bucket in _PARENT_PROCESS_PATTERNS:
        if pattern in needle:
            return bucket
    return "other"


def _classify_ancestor_name(raw: str) -> str:
    """Classify an ancestor command, recognising package runners.

    Extends the frozen parent classifier with one extra pass, and the order
    matters:

    1. Exact match on the basename (see ``_EXACT_PARENT_PATTERNS``) — for short
       tokens where a substring rule would collide with usernames and paths.
    2. Whatever ``_classify_parent_process_name`` decides (substring, FULL value).

    Pass 2 deliberately keeps the whole string rather than the basename: macOS
    app bundles name their helper binary something generic, so the identifying
    token lives in a parent directory — Cursor's helper is
    ``/Applications/Cursor.app/Contents/MacOS/Electron``, which only classifies
    as "cursor" if the directory survives.

    Same privacy contract as the frozen classifier: bucketed or dropped, never
    echoed.
    """
    needle = (raw or "").strip().lower()
    if not needle:
        return "other"
    basename = needle.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")
    exact = _EXACT_PARENT_PATTERNS.get(basename)
    if exact is not None:
        return exact
    return _classify_parent_process_name(needle)


def _read_process_name(pid: int) -> str:
    """Best-effort fetch of one process's command name. "" on any failure."""
    if _PLATFORM == "linux":
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    if _PLATFORM == "darwin":
        try:
            out = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    if _PLATFORM == "win32":
        # `tasklist` is the only always-present, dependency-free way to turn a
        # pid into an image name (wmic is deprecated and absent on Windows 11+).
        # CSV + /NH keeps parsing to a single split; the image name is field 0.
        #
        # CREATE_NO_WINDOW matters: an MCP server launched by a GUI host would
        # otherwise flash a console window on every boot. The flag only exists
        # on Windows, so it is read dynamically — see `_win_no_window_flag`.
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
                creationflags=_win_no_window_flag(),
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""
        line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
        # No match prints an INFO banner rather than a CSV row.
        if not line.startswith('"'):
            return ""
        return line.split('","', 1)[0].lstrip('"')
    return ""


def _win_no_window_flag() -> int:
    """``subprocess.CREATE_NO_WINDOW`` on Windows, 0 elsewhere.

    Read via ``getattr`` because the constant is only defined on Windows, and a
    direct reference would not typecheck on the POSIX hosts that run CI.
    """
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _read_parent_pid(pid: int) -> int | None:
    """The parent pid of ``pid``, or None if it can't be determined.

    Only needed to step over a launcher (see ``_LAUNCHER_BUCKETS``), so a None
    here degrades to "report the launcher itself", never to a crash.
    """
    if _PLATFORM == "linux":
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                stat = f.read()
        except OSError:
            return None
        # Field 2 (comm) is parenthesised and may itself contain spaces and
        # ')', so the only safe split point is the LAST ')'. ppid is then the
        # second whitespace-separated field of the remainder.
        _, _, rest = stat.rpartition(")")
        fields = rest.split()
        if len(fields) < 2:
            return None
        try:
            return int(fields[1])
        except ValueError:
            return None
    if _PLATFORM == "darwin":
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            return int(out.stdout.strip())
        except ValueError:
            return None
    # Windows: no dependency-free way to read a ppid (tasklist doesn't report
    # it). The launcher bucket is still reported; the grandparent is not.
    return None


@lru_cache(maxsize=1)
def _read_ancestor_parent_name() -> str:
    """Our parent's command name, on every platform. "" on any failure.

    Memoised: our parent cannot change for the life of the process, and each
    read costs a `ps` (macOS) or `tasklist` (Windows) subprocess. Several
    detectors consume it, so without the cache one fingerprint shells out
    repeatedly.
    """
    try:
        ppid = os.getppid()
    except OSError:
        return ""
    return _read_process_name(ppid)


def _read_parent_process_name() -> str:
    """FROZEN reader behind the long-lived ``parent_process`` field.

    Restricted to linux/darwin on purpose. ``_read_process_name`` also handles
    Windows now, but routing Windows through here would start populating
    ``parent_process`` on a platform where it has only ever reported "other" —
    silently changing a live BI series. The Windows-capable path feeds the newer
    ``host_process`` field instead, so the new signal arrives without disturbing
    the old one.
    """
    if _PLATFORM not in ("linux", "darwin"):
        return ""
    return _read_ancestor_parent_name()


def _detect_parent_process() -> str:
    """FROZEN: the bucket of our IMMEDIATE parent.

    Unchanged behaviour, deliberately — a package runner still reports as
    "other" here. ``host_process`` is the field that sees through it.
    """
    return _classify_parent_process_name(_read_parent_process_name())


def _detect_host_process() -> str:
    """NEW: bucket the nearest ancestor that identifies WHO launched us.

    Steps over a package runner (``uv``) to reach the MCP host behind it, and
    works on Windows. If the grandparent can't be read (Windows has no
    dependency-free ppid lookup) or doesn't classify, the launcher bucket is
    reported as-is — still strictly more than the "other" ``parent_process``
    reports for the same process. ``launcher`` preserves the fact that a runner
    was involved, so folding it away here loses nothing.
    """
    bucket = _classify_ancestor_name(_read_ancestor_parent_name())
    if bucket not in _LAUNCHER_BUCKETS:
        return bucket
    try:
        ppid = os.getppid()
    except OSError:
        return bucket
    gppid = _read_parent_pid(ppid)
    if gppid is None:
        return bucket
    grandparent = _classify_ancestor_name(_read_process_name(gppid))
    # Only take the grandparent when it actually identifies something; an
    # unrecognised ancestor is less informative than the known launcher.
    if grandparent in ("other", *_LAUNCHER_BUCKETS):
        return bucket
    return grandparent


def _detect_launcher() -> str:
    """NEW: ``"uv"`` when a package runner spawned us, else ``"none"``.

    Emitted alongside ``host_process`` so the uvx install path stays countable
    after ``_detect_host_process`` folds it away in favour of the host behind it.
    """
    bucket = _classify_ancestor_name(_read_ancestor_parent_name())
    return bucket if bucket in _LAUNCHER_BUCKETS else "none"

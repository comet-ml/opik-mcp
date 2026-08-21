"""Environment-fingerprint detectors merged into ``server_started``.

Every public/private helper returns a value from a hardcoded allowlist
(boolean strings ``"true"``/``"false"``, ``"unknown"``, or a bucket enum).
Raw paths, usernames, hostnames, and process command lines never leave
this module — see ``tests/test_analytics_environment.py`` and
``tests/test_analytics_privacy.py`` for the contract.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from functools import lru_cache

from opik_mcp.credential_identity import credential_digest

# ``sys.platform`` is a Literal type that mypy narrows per-host, so platform-
# dispatch branches get flagged unreachable on whichever host runs CI (Linux
# kills the macOS branch, macOS kills the Linux branch). Aliasing once to a
# plain ``str`` keeps the dispatch readable while stripping the narrowing,
# so the same source typechecks on every host.
_PLATFORM: str = sys.platform

# CI-platform env vars. Detection is OR across the list: any one set → "true".
_CI_ENV_VARS: tuple[str, ...] = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "JENKINS_URL",
)


def _detect_ci() -> str:
    return "true" if any(os.environ.get(v) for v in _CI_ENV_VARS) else "false"


def _detect_codespaces() -> str:
    return "true" if os.environ.get("CODESPACES") else "false"


def _detect_gitpod() -> str:
    return "true" if os.environ.get("GITPOD_WORKSPACE_ID") else "false"


def _detect_pipe_signals() -> dict[str, str]:
    """Stamp whether stdin/stdout are pipes (vs ttys)."""
    return {
        "stdin_is_pipe": str(not sys.stdin.isatty()).lower(),
        "stdout_is_pipe": str(not sys.stdout.isatty()).lower(),
    }


# Container detection. Linux-only — /proc/1/cgroup doesn't exist on
# macOS/Windows and detection there is unreliable (Lima/OrbStack don't all
# leak signals). Emit "unknown" rather than misleading "false".
#
# Paths are module-level so tests can monkeypatch them. The token set is
# intentionally small: matches the three most common container substrates
# (Docker, containerd via cgroup v1 names, Kubernetes pod controller paths).
_DOCKERENV_PATH = "/.dockerenv"
_CGROUP_PATH = "/proc/1/cgroup"
_CONTAINER_TOKENS = ("docker", "containerd", "kubepods")


def _detect_container() -> str:
    if _PLATFORM != "linux":
        return "unknown"
    try:
        if os.path.exists(_DOCKERENV_PATH):
            return "true"
        with open(_CGROUP_PATH, encoding="utf-8") as f:
            data = f.read().lower()
        return "true" if any(tok in data for tok in _CONTAINER_TOKENS) else "false"
    except OSError:
        # /proc/1/cgroup unreadable (rare — e.g. minimal init namespaces).
        # Best-effort: "false" rather than failing the emit.
        return "false"


# Launch-method substring patterns, matched against a separator-normalised
# lowercase `sys.executable`. Order matters: first match wins, so more-specific
# patterns ("uv/archive") must precede less-specific ones ("python").
#
# Patterns are written with FORWARD slashes only; `_normalise_exe_path` folds
# Windows backslashes before matching, so one table covers every platform.
# Without that, Windows reported `launch_method="unknown"` unconditionally
# (every pattern here was POSIX-shaped) — 6,645 starts of the 30-day fleet were
# dark for this field.
# FROZEN POSIX table — byte-identical to what first shipped. Do not add
# entries: a new pattern here could reclassify a path that currently reports
# "unknown", moving an existing series.
_LAUNCH_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("/uv/archive", "uvx"),
    ("/.local/share/uv/", "uvx"),
    ("/pipx/venvs/", "pipx"),
    ("/.venv/", "venv"),
    ("/venv/", "venv"),
    ("/usr/bin/", "system"),
    ("/usr/local/bin/", "system"),
)

# Windows table, consulted ONLY when running on win32 (see below). Kept separate
# from the POSIX table so it is impossible for a Windows pattern to reclassify a
# POSIX path — the POSIX result stays provably unchanged.
#
# uv's roots differ per OS: ~/.local/share/uv on Linux/macOS versus
# %LOCALAPPDATA%\uv\cache on Windows. System interpreters cover the python.org
# per-user installer, the all-users install under Program Files, and the
# Store/WindowsApps stub.
_WINDOWS_LAUNCH_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("/uv/archive", "uvx"),
    ("/uv/cache/", "uvx"),
    ("/uv/tools/", "uvx"),
    ("/pipx/venvs/", "pipx"),
    ("/.venv/", "venv"),
    ("/venv/", "venv"),
    ("/appdata/local/programs/python/", "system"),
    ("/program files/python", "system"),
    ("/windowsapps/", "system"),
)


def _normalise_exe_path(raw: str) -> str:
    """Lowercase a path and fold Windows separators to forward slashes.

    Never returned to a caller — only used as the matching subject.
    """
    return (raw or "").replace("\\", "/").lower()


def _detect_launch_method() -> str:
    """Bucket `sys.executable` into a launch-method enum.

    Platform-split on purpose. Every pattern in the frozen POSIX table needs a
    forward slash, and a Windows path (``C:\\Users\\...``) contains none — so
    this field returned the constant "unknown" on Windows for its entire life,
    carrying no information at all. The win32 branch fixes that blind spot
    (6,645 starts of the 30-day fleet) while leaving the POSIX branch running
    the original code over the original table, so no existing series moves.

    PRIVACY: never returns raw `sys.executable`. Anything not matching the
    allowlist falls through to "unknown" — the path is dropped, not echoed.
    """
    if _PLATFORM == "win32":
        win_exe = _normalise_exe_path(sys.executable)
        for needle, bucket in _WINDOWS_LAUNCH_METHOD_PATTERNS:
            if needle in win_exe:
                return bucket
        return "unknown"
    exe = (sys.executable or "").lower()
    for needle, bucket in _LAUNCH_METHOD_PATTERNS:
        if needle in exe:
            return bucket
    return "unknown"


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


_logger = logging.getLogger("opik_mcp.analytics.environment")


def _safe(fn: Callable[[], str], default: str) -> str:
    """Run a detector, falling back to ``default`` if it raises.

    Wraps each detector individually so one failure doesn't take the whole
    fingerprint down. Same fire-and-forget contract as ``track_event``.
    """
    try:
        return fn()
    except Exception:
        name = getattr(fn, "__name__", repr(fn))
        _logger.debug("environment detector %s raised", name, exc_info=True)
        return default


def collect_environment_fingerprint() -> dict[str, str]:
    """Bucketed environment signals to merge into ``server_started`` properties.

    Every value is from a hardcoded allowlist (booleans or bucket enums) —
    never a raw path, username, or process command. If a detector raises
    (filesystem oddity, missing tool, …), the field falls back to
    ``"unknown"`` so the aggregator never breaks the emit path.
    """
    out: dict[str, str] = {
        "is_ci": _safe(_detect_ci, "false"),
        "is_container": _safe(_detect_container, "unknown"),
        "is_codespaces": _safe(_detect_codespaces, "false"),
        "is_gitpod": _safe(_detect_gitpod, "false"),
        "launch_method": _safe(_detect_launch_method, "unknown"),
        # FROZEN field — immediate parent only. Kept bit-for-bit compatible with
        # every dashboard built on it. Read `host_process` for real attribution.
        "parent_process": _safe(_detect_parent_process, "unknown"),
        # NEW: the ancestor that actually identifies the MCP host — sees through
        # the `uvx` package runner and works on Windows, both of which leave
        # `parent_process` reporting "other".
        "host_process": _safe(_detect_host_process, "unknown"),
        # NEW: whether a package runner (uvx) sits between the host and us, so
        # the recommended install path stays countable after `host_process`
        # folds it away.
        "launcher": _safe(_detect_launcher, "unknown"),
    }
    try:
        out.update(_detect_pipe_signals())
    except Exception:
        _logger.debug("pipe-signals detector raised", exc_info=True)
        out["stdin_is_pipe"] = "unknown"
        out["stdout_is_pipe"] = "unknown"
    return out


@lru_cache(maxsize=1)
def cached_call_context_env() -> dict[str, str]:
    """Process-stable env subset stamped on every per-call analytics event.

    ``tool_called`` / ``ask_ollie_completed`` carry these so BI can segment by
    real-user cohort (``is_ci='false' AND is_container='false'``) on a single
    table — without joining each call back to ``server_started`` on
    ``install_id`` (a join that drops ~35% of calls in practice).

    Memoised: resolved once per process and reused on the hot path. Only the
    cheap, stable detectors are included — ``parent_process`` (a subprocess on
    macOS) stays startup-only on ``server_started`` and is deliberately not
    here.
    """
    # Imported lazily so this module stays free of ``config`` (and its
    # pydantic-settings machinery) at import time — ``identity`` pulls in
    # ``config``, heavier than this hot-path module wants to load eagerly.
    # There is no import cycle; this is purely about import cost.
    from opik_mcp.analytics.identity import install_id_was_freshly_generated

    return {
        "is_ci": _safe(_detect_ci, "false"),
        "is_container": _safe(_detect_container, "unknown"),
        "launch_method": _safe(_detect_launch_method, "unknown"),
        "install_id_freshly_generated": str(install_id_was_freshly_generated()).lower(),
    }


# OS-level machine identifiers. Read in platform order; the first that yields a
# non-empty value wins. All three are stable across reinstalls of opik-mcp and
# across a wiped HOME, which is the whole point (see ``env_id`` below).
_MACHINE_ID_PATHS: tuple[str, ...] = ("/etc/machine-id", "/var/lib/dbus/machine-id")
_WINDOWS_MACHINE_GUID_KEY = r"HKLM\SOFTWARE\Microsoft\Cryptography"


def _read_machine_id() -> str:
    """Best-effort OS machine identifier. "" on any failure — never raises.

    PRIVACY: the raw value never leaves this module; ``_detect_env_id`` hashes it.
    Deliberately contains NO user-derived data — no hostname, no OS username — so
    the digest identifies a machine and nothing about a person.
    """
    if _PLATFORM == "linux":
        for path in _MACHINE_ID_PATHS:
            try:
                with open(path, encoding="utf-8") as f:
                    value = f.read().strip()
                if value:
                    return value
            except OSError:
                continue
        return ""
    if _PLATFORM == "darwin":
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in out.stdout.splitlines():
            if "IOPlatformUUID" in line:
                _, _, tail = line.partition("=")
                return tail.strip().strip('"')
        return ""
    if _PLATFORM == "win32":
        try:
            out = subprocess.run(
                ["reg", "query", _WINDOWS_MACHINE_GUID_KEY, "/v", "MachineGuid"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
                creationflags=_win_no_window_flag(),
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""
        for line in out.stdout.splitlines():
            if "MachineGuid" in line:
                return line.split()[-1].strip()
        return ""
    return ""


@lru_cache(maxsize=1)
def _detect_env_id() -> tuple[str, str]:
    """``(digest, kind)`` for this machine. ``("", "unknown")`` when unreadable.

    Why this exists: ``install_id`` is the only machine identity we had, and it is
    a UUID in a file under HOME. That makes it fragile in two ways a funnel cares
    about — a reinstall or a wiped HOME mints a brand-new identity (inflating
    "new installs"), and an unwritable HOME collapses to the nil sentinel, merging
    every such deployment into one row.

    It is also the ONLY identity available to a large slice of users: local and
    self-hosted Opik run with auth disabled, so no credential and therefore no
    resolvable username exists for them — measured at ~18k successful tool calls
    across ~36 installs. A username can never cover those.

    Machine-scoped on purpose: one client per machine is the accepted grain, so
    the digest deliberately excludes the OS username. Two people sharing a box
    merge, which is fine, and it keeps user-derived data out of the hash entirely.

    Emits NOTHING rather than an unstable fallback. A hostname digest was
    considered and rejected: in a container the hostname is the container id, so
    it would churn per run while looking authoritative — the same failure mode as
    the nil ``install_id``. Absent is honest; churning is not.

    CONTAINERS READ ``unknown``, and that is the intended outcome — do not
    "fix" it by adding a fallback. Verified on ``python:3.13-slim``: two separate
    runs both read an EMPTY ``/etc/machine-id`` while the hostname differed
    (``42fbc2f195b2`` vs ``dda594ef58f6``). So a container is countable as "no
    stable identity" instead of either inflating (a per-run identity, which the
    hostname would have produced) or silently merging (one identity baked into a
    shared image). When querying, treat ``env_id_kind='unknown'`` as its own
    population rather than folding it in with ``machine``.

    Memoised — one subprocess per process on macOS/Windows, on the startup path.
    """
    raw = _safe(_read_machine_id, "").strip()
    if not raw:
        return ("", "unknown")
    return (credential_digest(raw), "machine")


def env_id() -> tuple[str, str]:
    """Public accessor for ``(digest, kind)``. See ``_detect_env_id``."""
    return _detect_env_id()


def _reset_detector_caches_for_tests() -> None:
    """Drop memoised detector state. Test-only — never call from production.

    ``_read_ancestor_parent_name`` is process-scoped by design, so a test that
    monkeypatches the platform or the underlying reader would otherwise see the
    previous test's parent name.
    """
    _read_ancestor_parent_name.cache_clear()
    _detect_env_id.cache_clear()
    cached_call_context_env.cache_clear()

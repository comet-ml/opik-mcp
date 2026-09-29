"""Environment-fingerprint detectors merged into ``server_started``.

Every public/private helper returns a value from a hardcoded allowlist
(boolean strings ``"true"``/``"false"``, ``"unknown"``, or a bucket enum).
Raw paths, usernames, hostnames, and process command lines never leave
this module — see ``tests/analytics/test_environment.py`` and
``tests/analytics/test_privacy.py`` for the contract.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from functools import lru_cache

from opik_mcp.analytics.identity import install_id_was_freshly_generated
from opik_mcp.analytics.process_ancestry import (
    _detect_host_process,
    _detect_launcher,
    _detect_parent_process,
    _read_ancestor_parent_name,
    _win_no_window_flag,
)
from opik_mcp.identity.store import credential_digest

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

    ``tool_called`` carries these so BI can segment by
    real-user cohort (``is_ci='false' AND is_container='false'``) on a single
    table — without joining each call back to ``server_started`` on
    ``install_id`` (a join that drops ~35% of calls in practice).

    Memoised: resolved once per process and reused on the hot path. Only the
    cheap, stable detectors are included — ``parent_process`` (a subprocess on
    macOS) stays startup-only on ``server_started`` and is deliberately not
    here.
    """
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

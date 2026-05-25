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
    if sys.platform != "linux":
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


# Launch-method substring patterns. Order matters: first match wins, so
# more-specific patterns ("uv/archive") must precede less-specific ones
# ("python"). The bucket value is the second element.
_LAUNCH_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("/uv/archive", "uvx"),
    ("/.local/share/uv/", "uvx"),
    ("/pipx/venvs/", "pipx"),
    ("/.venv/", "venv"),
    ("/venv/", "venv"),
    ("/usr/bin/", "system"),
    ("/usr/local/bin/", "system"),
)


def _detect_launch_method() -> str:
    """Bucket `sys.executable` into a launch-method enum.

    PRIVACY: never returns raw `sys.executable`. Anything not matching the
    allowlist falls through to "unknown" — the path is dropped, not echoed.
    """
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


def _classify_parent_process_name(raw: str) -> str:
    """Map a raw /proc/<ppid>/comm (or `ps -o comm=`) value to the allowlist.

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


def _read_parent_process_name() -> str:
    """Best-effort fetch of the parent process's command name.

    Returns "" on any failure. Never raises — the caller treats "" as
    "unknown parent" and buckets it to "other".
    """
    try:
        ppid = os.getppid()
    except OSError:
        return ""
    if sys.platform == "linux":
        try:
            with open(f"/proc/{ppid}/comm", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(ppid)],
                capture_output=True, text=True, timeout=1.0, check=False,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return ""


def _detect_parent_process() -> str:
    return _classify_parent_process_name(_read_parent_process_name())


_logger = logging.getLogger("opik_mcp.analytics.environment")


def collect_environment_fingerprint() -> dict[str, str]:
    """Bucketed environment signals to merge into ``server_started`` properties.

    Every value is from a hardcoded allowlist (booleans or bucket enums) —
    never a raw path, username, or process command. If a detector raises
    (filesystem oddity, missing tool, …), the field falls back to
    ``"unknown"`` so the aggregator never breaks the emit path.
    """
    # Wrap each detector individually so one failure doesn't take the whole
    # fingerprint down. Same fire-and-forget contract as ``track_event``.
    def _safe(fn, default: str) -> str:
        try:
            return fn()
        except Exception:
            _logger.debug("environment detector %s raised", fn.__name__, exc_info=True)
            return default

    out: dict[str, str] = {
        "is_ci": _safe(_detect_ci, "false"),
        "is_container": _safe(_detect_container, "unknown"),
        "is_codespaces": _safe(_detect_codespaces, "false"),
        "is_gitpod": _safe(_detect_gitpod, "false"),
        "launch_method": _safe(_detect_launch_method, "unknown"),
        "parent_process": _safe(_detect_parent_process, "unknown"),
    }
    try:
        out.update(_detect_pipe_signals())
    except Exception:
        _logger.debug("pipe-signals detector raised", exc_info=True)
        out["stdin_is_pipe"] = "unknown"
        out["stdout_is_pipe"] = "unknown"
    return out

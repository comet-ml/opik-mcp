"""Environment-fingerprint detectors merged into ``server_started``.

Every public/private helper returns a value from a hardcoded allowlist
(boolean strings ``"true"``/``"false"``, ``"unknown"``, or a bucket enum).
Raw paths, usernames, hostnames, and process command lines never leave
this module — see ``tests/test_analytics_environment.py`` and
``tests/test_analytics_privacy.py`` for the contract.
"""

from __future__ import annotations

import os
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

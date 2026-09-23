"""Install the current worktree as its own MCP server, `opik-<name>`.

The server is a snapshot: a venv under ~/.local/share/opik-mcp-<name> built
from this tree, registered at user scope so every session and worktree can
reach it. Re-run after changing code. Credentials come from OPIK_* environment
variables, then ~/.opik.config; the key is never taken as an argument and
never printed. Telemetry is always off.

Usage (through make):
    make install-branch [NAME=main] [WORKSPACE=other] [DRY_RUN=1]
    make uninstall-branch [NAME=...]
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class Credentials:
    url: str
    workspace: str
    api_key: str | None


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def server_name(branch: str) -> str:
    """`OPIK-8480-links` and `awkoy/OPIK-8480/links` -> `8480`; `main` -> `main`."""
    ticket = re.search(r"OPIK-(\d+)", branch, re.IGNORECASE)
    if ticket:
        return ticket.group(1)
    slug = re.sub(r"[^a-z0-9]+", "-", branch.rsplit("/", 1)[-1].lower()).strip("-")
    return slug or "branch"


def _from_config(home: Path) -> dict[str, str]:
    path = home / ".opik.config"
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser()
    parser.read(path)
    return dict(parser["opik"]) if parser.has_section("opik") else {}


def resolve_credentials(env: dict[str, str], home: Path, workspace: str | None) -> Credentials:
    config = _from_config(home)
    url = env.get("OPIK_URL") or config.get("url_override") or ""
    ws = workspace or env.get("OPIK_WORKSPACE") or config.get("workspace") or ""
    key = env.get("OPIK_API_KEY") or config.get("api_key") or None
    missing = [name for name, value in (("OPIK_URL", url), ("OPIK_WORKSPACE", ws)) if not value]
    local = urlparse(url).hostname in LOCAL_HOSTS
    if not key and url and not local:
        missing.append("OPIK_API_KEY")
    if missing:
        sys.exit(
            f"install-branch: no {', '.join(missing)}. Set the environment variables "
            "or run `opik configure` to write ~/.opik.config."
        )
    return Credentials(url=url.rstrip("/"), workspace=ws, api_key=key)


def _server_env(creds: Credentials) -> list[tuple[str, str]]:
    env = [("OPIK_URL", creds.url), ("OPIK_WORKSPACE", creds.workspace)]
    if creds.api_key:
        env.append(("OPIK_API_KEY", creds.api_key))
    env += [("OPIK_MCP_ANALYTICS_ENABLED", "false"), ("OPIK_MCP_SENTRY_ENABLED", "false")]
    return env


def _show(command: list[str], secret: str | None) -> str:
    text = shlex.join(command)
    return text.replace(secret, "***") if secret else text


def _run(
    command: list[str],
    *,
    dry_run: bool,
    secret: str | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> int:
    print(("would run: " if dry_run else "+ ") + _show(command, secret))
    if dry_run:
        return 0
    # An expected failure (removing an entry that is not there) is not news.
    stderr = None if check else subprocess.DEVNULL
    return subprocess.run(
        command, cwd=cwd, check=check, stdout=subprocess.DEVNULL, stderr=stderr
    ).returncode


def install(root: Path, name: str, creds: Credentials, venv: Path, dry_run: bool) -> None:
    server = f"opik-{name}"
    existed = (
        not dry_run
        and subprocess.run(["claude", "mcp", "get", server], capture_output=True).returncode == 0
    )
    _run(["make", "version"], dry_run=dry_run, cwd=root)
    _run(
        ["uv", "venv", "--quiet", "--allow-existing", "--python", "3.13", str(venv)],
        dry_run=dry_run,
    )
    _run(
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--reinstall",
            "--python",
            str(venv / "bin" / "python"),
            str(root),
        ],
        dry_run=dry_run,
    )
    _run(["claude", "mcp", "remove", "-s", "user", server], dry_run=dry_run, check=False)
    add = ["claude", "mcp", "add", "-s", "user", server]
    for key, value in _server_env(creds):
        add += ["-e", f"{key}={value}"]
    add += ["--", str(venv / "bin" / "opik-mcp")]
    _run(add, dry_run=dry_run, secret=creds.api_key)
    if dry_run:
        return
    if existed:
        print(f"Reinstalled {server}. In Claude Code run /mcp and reconnect {server}.")
    else:
        print(f"Registered {server}. Restart Claude Code to load it.")


def uninstall(name: str, venv: Path, dry_run: bool) -> None:
    _run(["claude", "mcp", "remove", "-s", "user", f"opik-{name}"], dry_run=dry_run, check=False)
    print(("would run: " if dry_run else "+ ") + shlex.join(["rm", "-rf", str(venv)]))
    if not dry_run:
        shutil.rmtree(venv, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--name", help="server suffix; defaults to the ticket in the branch name")
    parser.add_argument("--workspace", help="Opik workspace; overrides env and ~/.opik.config")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = parser.parse_args()

    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel"))
    name = args.name or server_name(_git(root, "branch", "--show-current") or "detached")
    home = Path.home()
    venv = home / ".local" / "share" / f"opik-mcp-{name}"
    if args.action == "uninstall":
        uninstall(name, venv, args.dry_run)
        return
    creds = resolve_credentials(dict(os.environ), home, args.workspace)
    install(root, name, creds, venv, args.dry_run)


if __name__ == "__main__":
    main()

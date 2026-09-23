"""Install the current worktree as its own MCP server, opik-<name>."""

# The server is a snapshot: a venv under ~/.local/share/opik-mcp-<name> built
# from this tree, registered at user scope. The key goes to `claude mcp add`
# as an `-e` argument, the only way that CLI takes one, so it is visible in
# `ps` while that command runs; it is never taken as an argument to this
# script and never printed.

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
    api_url: str
    workspace: str
    api_key: str | None


@dataclass(frozen=True)
class Target:
    server: str
    venv: Path

    @classmethod
    def named(cls, name: str) -> Target:
        return cls(f"opik-{name}", Path.home() / ".local" / "share" / f"opik-mcp-{name}")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def server_name(branch: str) -> str:
    ticket = re.search(r"OPIK-(\d+)", branch, re.IGNORECASE)
    if ticket:
        return ticket.group(1)
    slug = re.sub(r"[^a-z0-9]+", "-", branch.rsplit("/", 1)[-1].lower()).strip("-")
    return slug or "branch"


def _config_file(home: Path) -> dict[str, str]:
    path = home / ".opik.config"
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    return dict(parser["opik"]) if parser.has_section("opik") else {}


def resolve_credentials(env: dict[str, str], home: Path, workspace: str | None) -> Credentials:
    # URL and key come from one source, so an env URL never picks up the
    # config file's key and sends it to a host it was not set up for.
    if env.get("OPIK_URL"):
        url, key, ws = env["OPIK_URL"], env.get("OPIK_API_KEY"), env.get("OPIK_WORKSPACE")
    else:
        config = _config_file(home)
        url = config.get("url_override", "")
        key = config.get("api_key")
        ws = env.get("OPIK_WORKSPACE") or config.get("workspace")
    ws = workspace or ws or ""
    missing = [name for name, value in (("OPIK_URL", url), ("OPIK_WORKSPACE", ws)) if not value]
    if url and not key and urlparse(url).hostname not in LOCAL_HOSTS:
        missing.append("OPIK_API_KEY")
    if missing:
        sys.exit(
            f"install-branch: no {', '.join(missing)}. Set the environment variables "
            "or run `opik configure` to write ~/.opik.config."
        )
    return Credentials(api_url=url.rstrip("/"), workspace=ws, api_key=key or None)


def _server_env(creds: Credentials) -> list[tuple[str, str]]:
    env = [("OPIK_URL", creds.api_url), ("OPIK_WORKSPACE", creds.workspace)]
    if creds.api_key:
        env.append(("OPIK_API_KEY", creds.api_key))
    return [*env, ("OPIK_MCP_ANALYTICS_ENABLED", "false"), ("OPIK_MCP_SENTRY_ENABLED", "false")]


class Runner:
    def __init__(self, *, dry_run: bool, secret: str | None) -> None:
        self.dry_run = dry_run
        self.secret = secret

    def redacted(self, text: str) -> str:
        return text.replace(self.secret, "***") if self.secret else text

    def announce(self, command: list[str]) -> None:
        prefix = "would run: " if self.dry_run else "+ "
        print(prefix + self.redacted(shlex.join(command)))

    def run(self, command: list[str], *, cwd: Path | None = None, allow_fail: bool = False) -> None:
        self.announce(command)
        if self.dry_run:
            return
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        if result.returncode and not allow_fail:
            # The child's own error output may echo its arguments, key included.
            sys.exit(
                f"install-branch: failed: {self.redacted(shlex.join(command))}\n"
                f"{self.redacted(result.stderr.strip())}"
            )


def install(root: Path, target: Target, creds: Credentials, *, dry_run: bool) -> None:
    runner = Runner(dry_run=dry_run, secret=creds.api_key)
    existed = not dry_run and (
        subprocess.run(["claude", "mcp", "get", target.server], capture_output=True).returncode == 0
    )
    python = target.venv / "bin" / "python"
    runner.run(["make", "version"], cwd=root)
    runner.run(["uv", "venv", "--quiet", "--allow-existing", "--python", "3.13", str(target.venv)])
    runner.run(
        ["uv", "pip", "install", "--quiet", "--reinstall", "--python", str(python), str(root)]
    )
    runner.run(["claude", "mcp", "remove", "-s", "user", target.server], allow_fail=True)
    add = ["claude", "mcp", "add", "-s", "user", target.server]
    for key, value in _server_env(creds):
        add += ["-e", f"{key}={value}"]
    runner.run([*add, "--", str(target.venv / "bin" / "opik-mcp")])
    if dry_run:
        return
    if existed:
        print(f"Reinstalled {target.server}. In Claude Code run /mcp and reconnect it.")
    else:
        print(f"Registered {target.server}. Restart Claude Code to load it.")


def uninstall(target: Target, *, dry_run: bool) -> None:
    runner = Runner(dry_run=dry_run, secret=None)
    runner.run(["claude", "mcp", "remove", "-s", "user", target.server], allow_fail=True)
    runner.announce(["rm", "-rf", str(target.venv)])
    if not dry_run:
        shutil.rmtree(target.venv, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--name", help="server suffix; defaults to the ticket in the branch name")
    parser.add_argument("--workspace", help="Opik workspace; overrides env and ~/.opik.config")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = parser.parse_args()

    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel"))
    branch = _git(root, "branch", "--show-current") or "detached"
    target = Target.named(args.name or server_name(branch))
    if args.action == "uninstall":
        uninstall(target, dry_run=args.dry_run)
        return
    creds = resolve_credentials(dict(os.environ), Path.home(), args.workspace)
    install(root, target, creds, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

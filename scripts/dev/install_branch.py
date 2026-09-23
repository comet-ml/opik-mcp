"""Run a worktree as an Opik MCP server: installed for this repo, or for one dogfood run."""

# Every registered server puts its instructions (about 1,150 tokens) into every
# session that loads it, so nothing here registers at user scope:
#
# - install: a snapshot venv registered at local scope, which Claude Code keys
#   by repo, so it loads only in sessions inside this repo.
# - dogfood-*: two venvs (this branch and origin/main) described in a private
#   --mcp-config file and loaded only by the one headless run that needs them.
#
# The key is written as ${OPIK_API_KEY}, which Claude Code expands at launch.
# The one exception: `install` with the key only in ~/.opik.config has to store
# it, and says so. The key is never an argument to this script, and it is
# redacted from everything printed.

from __future__ import annotations

import argparse
import configparser
import json
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
KEY_REFERENCE = "${OPIK_API_KEY}"
READ_TOOLS = ("read", "list", "schema", "read_skill")


def _data_dir() -> Path:
    return Path.home() / ".local" / "share"


def _installed(name: str) -> tuple[str, Path]:
    return f"opik-{name}", _data_dir() / f"opik-mcp-{name}"


@dataclass(frozen=True)
class Credentials:
    api_url: str
    workspace: str
    api_key: str | None
    key_in_env: bool


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
        key_in_env = bool(key)
    else:
        config = _config_file(home)
        url = config.get("url_override", "")
        key = config.get("api_key")
        ws = env.get("OPIK_WORKSPACE") or config.get("workspace")
        key_in_env = False
    ws = workspace or ws or ""
    missing = [name for name, value in (("OPIK_URL", url), ("OPIK_WORKSPACE", ws)) if not value]
    if url and not key and urlparse(url).hostname not in LOCAL_HOSTS:
        missing.append("OPIK_API_KEY")
    if missing:
        sys.exit(
            f"install-branch: no {', '.join(missing)}. Set the environment variables "
            "or run `opik configure` to write ~/.opik.config."
        )
    return Credentials(
        api_url=url.rstrip("/"), workspace=ws, api_key=key or None, key_in_env=key_in_env
    )


def server_env(creds: Credentials, *, key_value: str | None) -> dict[str, str]:
    env = {"OPIK_URL": creds.api_url, "OPIK_WORKSPACE": creds.workspace}
    if creds.api_key and key_value:
        env["OPIK_API_KEY"] = key_value
    return env | {"OPIK_MCP_ANALYTICS_ENABLED": "false", "OPIK_MCP_SENTRY_ENABLED": "false"}


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


def build_venv(runner: Runner, tree: Path, venv: Path) -> Path:
    runner.run(["make", "version"], cwd=tree)
    runner.run(["uv", "venv", "--quiet", "--allow-existing", "--python", "3.13", str(venv)])
    python = venv / "bin" / "python"
    runner.run(
        ["uv", "pip", "install", "--quiet", "--reinstall", "--python", str(python), str(tree)]
    )
    return venv / "bin" / "opik-mcp"


def install(root: Path, name: str, creds: Credentials, *, dry_run: bool) -> None:
    server, venv = _installed(name)
    runner = Runner(dry_run=dry_run, secret=creds.api_key)
    registered = not dry_run and (
        subprocess.run(["claude", "mcp", "get", server], cwd=root, capture_output=True).returncode
        == 0
    )
    binary = build_venv(runner, root, venv)
    # An older user-scope entry of the same name would load in every project.
    runner.run(["claude", "mcp", "remove", "-s", "user", server], cwd=root, allow_fail=True)
    runner.run(["claude", "mcp", "remove", "-s", "local", server], cwd=root, allow_fail=True)
    key_value = KEY_REFERENCE if creds.key_in_env else creds.api_key
    add = ["claude", "mcp", "add", "-s", "local", server]
    for key, value in server_env(creds, key_value=key_value).items():
        add += ["-e", f"{key}={value}"]
    runner.run([*add, "--", str(binary)], cwd=root)
    if creds.api_key and not creds.key_in_env:
        print(
            "note: the key came from ~/.opik.config, so it is stored in ~/.claude.json. "
            "Export OPIK_API_KEY and reinstall to store only a reference."
        )
    if dry_run:
        return
    if registered:
        print(
            f"Reinstalled {server} for this repo. In Claude Code run /mcp and reconnect it; "
            "restart if it moved from user scope."
        )
    else:
        print(f"Registered {server} for this repo. Restart Claude Code to load it.")


def uninstall(root: Path, name: str, *, dry_run: bool) -> None:
    server, venv = _installed(name)
    runner = Runner(dry_run=dry_run, secret=None)
    for scope in ("local", "user"):
        runner.run(["claude", "mcp", "remove", "-s", scope, server], cwd=root, allow_fail=True)
    runner.announce(["rm", "-rf", str(venv)])
    if not dry_run:
        shutil.rmtree(venv, ignore_errors=True)


@dataclass(frozen=True)
class Dogfood:
    root: Path
    name: str

    @property
    def home(self) -> Path:
        return _data_dir() / f"opik-mcp-dogfood-{self.name}"

    @property
    def config(self) -> Path:
        return self.home / "mcp.json"

    @property
    def base_tree(self) -> Path:
        return self.root / ".claude" / "worktrees" / f"dogfood-base-{self.name}"


def dogfood_prepare(dogfood: Dogfood, creds: Credentials, *, dry_run: bool) -> None:
    runner = Runner(dry_run=dry_run, secret=creds.api_key)
    root, base = dogfood.root, dogfood.base_tree
    runner.run(["git", "fetch", "--quiet", "origin", "main"], cwd=root)
    if base.exists():
        runner.run(["git", "checkout", "--quiet", "--detach", "origin/main"], cwd=base)
    else:
        runner.run(
            ["git", "worktree", "add", "--quiet", "--detach", str(base), "origin/main"], cwd=root
        )
    servers = {
        "opik-branch": build_venv(runner, root, dogfood.home / "branch"),
        "opik-base": build_venv(runner, base, dogfood.home / "base"),
    }
    env = server_env(creds, key_value=KEY_REFERENCE)
    config = {
        "mcpServers": {n: {"command": str(b), "args": [], "env": env} for n, b in servers.items()}
    }
    text = json.dumps(config, indent=2)
    print(("would write " if dry_run else "wrote ") + str(dogfood.config))
    if dry_run:
        print(text)
        return
    dogfood.config.parent.mkdir(parents=True, exist_ok=True)
    dogfood.config.write_text(text)


def dogfood_run(
    root: Path, creds: Credentials, prompt_file: Path, config: Path, *, dry_run: bool
) -> None:
    if not config.is_file() and not dry_run:
        sys.exit(f"install-branch: no {config}. Run dogfood-prepare first.")
    command = [
        "claude",
        "-p",
        prompt_file.read_text(),
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        # Read tools only, so a flow can never write to the workspace.
        "--allowedTools",
        *(f"mcp__{s}__{t}" for s in ("opik-branch", "opik-base") for t in READ_TOOLS),
        "Read",
        "Grep",
        "Glob",
    ]
    runner = Runner(dry_run=dry_run, secret=creds.api_key)
    if dry_run:
        runner.announce([*command[:2], f"<{prompt_file}>", *command[3:]])
        return
    # The key reaches the headless session through its environment only.
    env = dict(os.environ)
    if creds.api_key:
        env["OPIK_API_KEY"] = creds.api_key
    result = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    print(runner.redacted(result.stdout))
    if result.returncode:
        sys.exit(f"install-branch: dogfood run failed\n{runner.redacted(result.stderr.strip())}")


def dogfood_clean(dogfood: Dogfood, *, dry_run: bool) -> None:
    runner = Runner(dry_run=dry_run, secret=None)
    if dogfood.base_tree.exists():
        runner.run(
            ["git", "worktree", "remove", "--force", str(dogfood.base_tree)], cwd=dogfood.root
        )
    runner.announce(["rm", "-rf", str(dogfood.home)])
    if not dry_run:
        shutil.rmtree(dogfood.home, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["install", "uninstall", "dogfood-prepare", "dogfood-run", "dogfood-clean"],
    )
    parser.add_argument("--name", help="server suffix; defaults to the ticket in the branch name")
    parser.add_argument("--workspace", help="Opik workspace; overrides env and ~/.opik.config")
    parser.add_argument("--prompt-file", type=Path, help="dogfood-run: the flows to run")
    parser.add_argument("--config", type=Path, help="dogfood-run: use this MCP config instead")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = parser.parse_args()

    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel"))
    name = args.name or server_name(_git(root, "branch", "--show-current") or "detached")
    dogfood = Dogfood(root=root, name=name)
    if args.action == "uninstall":
        uninstall(root, name, dry_run=args.dry_run)
        return
    if args.action == "dogfood-clean":
        dogfood_clean(dogfood, dry_run=args.dry_run)
        return
    creds = resolve_credentials(dict(os.environ), Path.home(), args.workspace)
    if args.action == "install":
        install(root, name, creds, dry_run=args.dry_run)
    elif args.action == "dogfood-prepare":
        dogfood_prepare(dogfood, creds, dry_run=args.dry_run)
    else:
        if not args.prompt_file:
            sys.exit("install-branch: dogfood-run needs --prompt-file")
        config = args.config or dogfood.config
        dogfood_run(root, creds, args.prompt_file, config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

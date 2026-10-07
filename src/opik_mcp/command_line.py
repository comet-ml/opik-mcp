"""What the ``opik-mcp`` command does with its arguments before it serves.

The server takes its settings from env vars, and an MCP client starts it with
no arguments. ``--help`` and ``--version`` answer and exit, so an agent that
probes the command learns how to install it instead of starting a server that
waits on stdin; ``--check`` tries the settings in its env and exits. Any other
argument is ignored, as before, with a warning.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

import httpx
from pydantic import ValidationError

from opik_mcp.analytics.identity import OPIK_MCP_VERSION
from opik_mcp.client.base import resolve_opik_config
from opik_mcp.client.errors import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.client.errors.hints import address, no_api_at, unreachable
from opik_mcp.client.opik import make_opik_client
from opik_mcp.config import MissingConfigError, get_settings

HELP_ARGUMENTS = frozenset({"-h", "--help"})
VERSION_ARGUMENTS = frozenset({"-V", "--version"})
CHECK_ARGUMENT = "--check"

#: Long enough for a cold backend, short enough for an agent's shell command.
CHECK_TIMEOUT_SECONDS = 10.0

# Written for a coding agent asked to install the server. The command lines
# also appear in README.md (tests/repo/test_help_matches_readme.py).
HELP_TEXT = """\
opik-mcp: the MCP server for Opik, Comet's LLM observability platform.

This is not an interactive command. An MCP client (Claude Code, Codex,
Cursor, VS Code, ...) starts it and talks to it over stdin/stdout. To install
it, register it with the client, for the case that matches the user's Opik:

1. Opik running on this machine (open source), if
   http://localhost:5173/api/is-alive/ping answers. No API key, no workspace.
     claude mcp add --scope user opik-mcp --env OPIK_URL=http://localhost:5173/api -- uvx opik-mcp
     codex mcp add opik-mcp --env OPIK_URL=http://localhost:5173/api -- uvx opik-mcp

2. Opik Cloud (www.comet.com): the hosted server, run by Comet, instead of
   this package. No API key; the user signs in in the browser.
     claude mcp add --scope user --transport http opik-mcp https://www.comet.com/opik/api/v1/mcp
     claude mcp login opik-mcp
     codex mcp add opik-mcp --url https://www.comet.com/opik/api/v1/mcp

3. Self-hosted: ask the user for the URL, then register as in 1 with
   open-source Opik:  OPIK_URL=https://<host>/api
   a Comet platform:  COMET_URL_OVERRIDE=https://<host>, OPIK_WORKSPACE
                      and OPIK_API_KEY

Before registering 1 or 3, check the env you will pass, OPIK_API_KEY from
the shell. It exits 0 or says what to fix:
  OPIK_URL=http://localhost:5173/api uvx opik-mcp --check

Not sure which applies? Ask the user. A failed localhost check can mean the
shell has no network access, as in a sandbox, rather than that Opik is down.
Don't ask the user to paste an API key into the chat; pass it from the shell,
as --env OPIK_API_KEY="$OPIK_API_KEY", or, if it is not set there, let the
user run the command. Don't guess the workspace: it is the
segment after /opik/ in the user's Opik URL. If opik-mcp is already
registered, tell the user before replacing it. Claude Code refuses to add
over it until it is removed:
  claude mcp remove opik-mcp --scope user

Then read only Claude Code's status line, since the full output prints the
env, API key included:
  claude mcp get opik-mcp | grep Status
Clients load MCP servers when a session starts: ask the user to start a new
session, then try "list my Opik projects".

Several clients at once, with the Opik skill pack (without a terminal, add
--ai-client <client>):
  uvx opik mcp configure
Every client and setting: https://github.com/comet-ml/opik-mcp#readme
"""

MIGRATION_GUIDE_URL = (
    "https://github.com/comet-ml/opik-mcp/blob/main/legacy/typescript/MIGRATION.md"
)

# The TypeScript server's flags (tag legacy-typescript-final, src/config.ts and
# src/cli.ts). A config migrated from `npx opik-mcp` keeps them, and the server
# then starts without the setting they carried.
TYPESCRIPT_FLAG_ENV_VARS = {
    "--apiKey": "OPIK_API_KEY",
    "--key": "OPIK_API_KEY",
    "--apiUrl": "OPIK_URL",
    "--url": "OPIK_URL",
    "--workspace": "OPIK_WORKSPACE",
    "--ws": "OPIK_WORKSPACE",
    "--debug": "OPIK_MCP_LOG_LEVEL=DEBUG",
    "--transport": "OPIK_MCP_TRANSPORT",
    "-t": "OPIK_MCP_TRANSPORT",
    "--streamableHttpPort": "OPIK_MCP_PORT",
    "--port": "OPIK_MCP_PORT",
    "-p": "OPIK_MCP_PORT",
    "--streamableHttpHost": "OPIK_MCP_HOST",
}

TERMINAL_HINT = (
    "opik-mcp is an MCP server: an MCP client starts it and talks to it over "
    "stdin/stdout, and it is waiting for one now. Run `uvx opik-mcp --help` for "
    "how to install it."
)


def reply_for(arguments: Sequence[str]) -> str | None:
    """The text to print and exit with instead of serving, or None to serve."""
    if HELP_ARGUMENTS.intersection(arguments):
        return HELP_TEXT
    if VERSION_ARGUMENTS.intersection(arguments):
        return f"opik-mcp {OPIK_MCP_VERSION}"
    return None


def run_check() -> int:
    """`--check`: one authenticated call with the settings in this process's env.

    For an agent to run before registering the local server, with the env it is
    about to pass: Claude Code reports Connected even with a wrong key, and Codex
    starts nothing before a session. Prints one line; returns the exit status.
    """
    try:
        settings = get_settings()
        base_url, _, workspace = resolve_opik_config(settings)
        client = make_opik_client(settings, timeout=CHECK_TIMEOUT_SECONDS)
        page = asyncio.run(client.list_projects(size=1))
    except ValidationError as err:
        invalid = ", ".join(str(error["loc"][0]).upper() for error in err.errors())
        return _report(f"An opik-mcp setting is invalid: {invalid}.", status=1)
    except (
        MissingConfigError,
        OpikAuthError,
        OpikNotFoundError,
        OpikServerError,
        OpikValidationError,
    ) as err:
        # A 404, or a web page where the API should be: the path is not Opik's.
        wrong_path = isinstance(err, OpikNotFoundError) or isinstance(err.__cause__, ValueError)
        return _report(no_api_at(base_url) if wrong_path else str(err), status=1)
    except httpx.HTTPError as err:
        return _report(f"Could not reach Opik to list projects: {unreachable(err)}", status=1)
    # On a Comet platform an unset workspace is the account's default, which may
    # not be the one meant.
    unset = not settings.comet_workspace and base_url.endswith("/opik/api")
    return _report(
        f"OK: Opik at {address(base_url)}, workspace {workspace}"
        f"{' (OPIK_WORKSPACE not set, so the account default)' if unset else ''}, "
        f"{page.get('total', 0)} projects visible.",
        status=0,
    )


def _report(line: str, *, status: int) -> int:
    sys.stdout.write(f"{line}\n")
    return status


def startup_warnings(arguments: Sequence[str], *, stdin_is_a_terminal: bool) -> list[str]:
    """What to log before serving: ignored arguments, and a hint for a person at a terminal."""
    warnings = []
    ignored = _describe_ignored_arguments(arguments)
    if ignored:
        warnings.append(
            "opik-mcp takes its settings from env vars and ignores command-line "
            f"arguments: {ignored}. See {MIGRATION_GUIDE_URL}"
        )
    if stdin_is_a_terminal:
        warnings.append(TERMINAL_HINT)
    return warnings


def _describe_ignored_arguments(arguments: Sequence[str]) -> str:
    # Only flag names are repeated. A value can be the API key itself, also
    # when it is glued to the flag: --apiKey=<key>, -k<key>.
    described_arguments = []
    ignored_value_count = 0
    for argument in arguments:
        if argument == "--":
            continue
        if argument.startswith("--"):
            flag = argument.split("=", 1)[0]
        elif argument.startswith("-"):
            flag = argument[:2]
        else:
            ignored_value_count += 1
            continue
        env_var = TYPESCRIPT_FLAG_ENV_VARS.get(_camel_case(flag))
        described_arguments.append(f"{flag} (use {env_var})" if env_var else flag)
    if ignored_value_count:
        plural = "s" if ignored_value_count > 1 else ""
        described_arguments.append(f"{ignored_value_count} other value{plural}")
    return ", ".join(described_arguments)


def _camel_case(flag: str) -> str:
    # The TypeScript server also took kebab case: --api-key for --apiKey.
    if not flag.startswith("--"):
        return flag
    first, *rest = flag[2:].split("-")
    return "--" + first + "".join(part[:1].upper() + part[1:] for part in rest)

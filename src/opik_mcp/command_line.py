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
Cursor, VS Code, ...) starts it and talks to it over stdin/stdout.

To install it for the user, ask only what this machine can't tell you, one
question per message, each answer before the next, all before step 4:

1. See what is there:
     uvx opik mcp status
   It shows the Opik saved in ~/.opik.config (not the key) and the clients
   with the server. In Claude Code, claude mcp list also shows claude.ai
   connectors. A server for https://www.comet.com/opik/api/v1/mcp under any
   name is it: don't add another (for the skill pack alone, run
   npx skills add comet-ml/opik-skills -g --all). The installer replaces an
   opik-mcp entry without asking: ask the user first.
2. Find their Opik. Saved: use it, and say which; the installer reads its
   key. Else try http://localhost:5173/api/is-alive/ping (a sandboxed shell
   may have no network). Else ask, and don't pick one yourself: Opik Cloud,
   an open-source Opik (URL), or a self-hosted Comet (URL and workspace)?
3. Always ask whether to install the Opik skill pack: skills for every
   project that load into later sessions as instructions.
4. Run the installer for your client (or codex, cursor, vscode, opencode),
   with --skills or --no-skills from the answer, never neither: it adds the
   pack by default. On Opik Cloud it opens a browser sign-in and waits a
   minute: tell the user first.
     uvx opik mcp configure --ai-client claude-code --skills
   Saved or local Opik: run it as is (--deployment drops the saved config
   and its key). Only for an Opik the user named, add --deployment:
     cloud                              Opik Cloud, www.comet.com
     local --url <url>                  open-source Opik
     self-hosted --url <url> --workspace <segment after /opik/ in the URL>
                                        a Comet platform; key from the shell
   If it fails, tell the user what it printed; don't switch to another Opik.
5. If it says "not signed in yet", give the user this to run rather than
   running it; it waits on the browser:
     claude mcp login opik-mcp
6. Say what was installed. Clients load servers and skills when a session
   starts: ask the user to start one, then try "list my Opik projects".

For a client the installer doesn't know, register by hand. Opik Cloud:
  claude mcp add --scope user --transport http opik-mcp https://www.comet.com/opik/api/v1/mcp
Other Opik: this package, OPIK_API_KEY from the shell, never the chat
(a Comet platform sets COMET_URL_OVERRIDE and OPIK_WORKSPACE, not OPIK_URL):
  OPIK_URL=http://localhost:5173/api uvx opik-mcp --check
  claude mcp add --scope user opik-mcp --env OPIK_URL=http://localhost:5173/api -- uvx opik-mcp
Read only the status line; the full output prints the API key:
  claude mcp get opik-mcp | grep Status
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

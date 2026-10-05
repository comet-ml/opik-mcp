"""What the ``opik-mcp`` command does with its arguments before it serves.

The server takes its settings from env vars, and an MCP client starts it with
no arguments. ``--help`` and ``--version`` answer and exit, so an agent that
probes the command learns how to install it instead of starting a server that
waits on stdin. Any other argument is ignored, as before, with a warning.
"""

from __future__ import annotations

from collections.abc import Sequence

from opik_mcp.analytics.identity import OPIK_MCP_VERSION

HELP_ARGUMENTS = frozenset({"-h", "--help"})
VERSION_ARGUMENTS = frozenset({"-V", "--version"})

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

Not sure which applies? Ask the user. A failed localhost check can mean the
shell has no network access, as in a sandbox, rather than that Opik is down.
Don't ask the user to paste an API key into the chat; pass it from the shell,
as --env OPIK_API_KEY="$OPIK_API_KEY". Don't guess the workspace: it is the
segment after /opik/ in the user's Opik URL. If opik-mcp is already
registered, tell the user before replacing it. Claude Code refuses to add
over it until it is removed:
  claude mcp remove opik-mcp --scope user

Then read only Claude Code's status line, since the full output prints the
env, API key included:
  claude mcp get opik-mcp | grep Status
Clients load MCP servers when a session starts: ask the user to start a new
session, then try "list my Opik projects".

Several clients at once, with the Opik skill pack:
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
    "stdin/stdout, and it is waiting for one now. Run `opik-mcp --help` for how "
    "to install it."
)


def reply_for(arguments: Sequence[str]) -> str | None:
    """The text to print and exit with instead of serving, or None to serve."""
    if HELP_ARGUMENTS.intersection(arguments):
        return HELP_TEXT
    if VERSION_ARGUMENTS.intersection(arguments):
        return f"opik-mcp {OPIK_MCP_VERSION}"
    return None


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
    # Only flag names are repeated. A value can be the API key itself.
    flags = []
    values = 0
    for argument in arguments:
        if argument.startswith("-") and argument != "--":
            flag = argument.split("=", 1)[0]
            env_var = TYPESCRIPT_FLAG_ENV_VARS.get(_camel_case(flag))
            flags.append(f"{flag} (use {env_var})" if env_var else flag)
        elif argument != "--":
            values += 1
    if values:
        flags.append(f"{values} other value{'s' if values > 1 else ''}")
    return ", ".join(flags)


def _camel_case(flag: str) -> str:
    # The TypeScript server also took kebab case: --api-key for --apiKey.
    if not flag.startswith("--"):
        return flag
    first, *rest = flag[2:].split("-")
    return "--" + first + "".join(part[:1].upper() + part[1:] for part in rest)

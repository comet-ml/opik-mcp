"""``opik-mcp --help`` gives an agent the same install commands as the README.

The help text and the README's install sections are two copies of one set of
instructions, read by agents that land on either. These keep the commands, the
env vars and the size of the help in line.
"""

from __future__ import annotations

import re
from pathlib import Path

from opik_mcp.command_line import HELP_TEXT, TYPESCRIPT_FLAG_ENV_VARS
from tests.repo.settings_env import env_names_the_server_reads

README = Path(__file__).resolve().parents[2] / "README.md"

#: An agent reads the whole help when it runs `opik-mcp --help`, so it is
#: paid for in context once per probe.
#: 2026-10-07: 2_500 -> 3_000 for the agent steps: find the user's Opik before
#: asking, ask about the skill pack, run `opik mcp configure`.
HELP_BUDGET_BYTES = 3_000

_COMMAND_LINE = re.compile(
    r"^\s*((?:[A-Z_]+=\S+ )*(?:(?:claude|codex) mcp|uvx) \S.*)$", re.MULTILINE
)
_ENV_VAR = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")


def _help_commands() -> list[str]:
    return [command.strip() for command in _COMMAND_LINE.findall(HELP_TEXT)]


def test_the_help_carries_install_commands() -> None:
    assert len(_help_commands()) >= 5, (
        f"src/opik_mcp/command_line.py HELP_TEXT has {_help_commands()} as command lines; "
        "the drift check below would pass without them. Keep each command on its own "
        "indented line."
    )


def test_every_command_in_the_help_is_in_the_readme() -> None:
    readme = README.read_text()
    missing = [command for command in _help_commands() if command not in readme]
    assert not missing, (
        f"src/opik_mcp/command_line.py HELP_TEXT has commands README.md does not: {missing}. "
        "The help and the README's install sections give agents the same commands; "
        "change both."
    )


def test_every_env_var_the_help_names_is_one_the_server_reads() -> None:
    named = set(_ENV_VAR.findall(HELP_TEXT)) | {
        env_var.split("=", 1)[0] for env_var in TYPESCRIPT_FLAG_ENV_VARS.values()
    }
    unread = named - env_names_the_server_reads()
    assert not unread, (
        f"src/opik_mcp/command_line.py names env vars that Settings in "
        f"src/opik_mcp/config.py never reads: {sorted(unread)}."
    )


def test_the_help_stays_within_its_budget() -> None:
    size = len(HELP_TEXT.encode())
    assert size <= HELP_BUDGET_BYTES, (
        f"src/opik_mcp/command_line.py HELP_TEXT is {size} bytes, over the "
        f"{HELP_BUDGET_BYTES}-byte budget in tests/repo/test_help_matches_readme.py. "
        "Cut it, or raise the budget with a note saying why."
    )

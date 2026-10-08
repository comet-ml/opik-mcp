"""One-off `uvx` commands run the latest release; the launch command does not.

Without a version, uvx reuses the copy it cached on the first run, so a
machine that once ran an old release keeps its old install steps and
installer, and opik-mcp 3.0.0 serves on `--check` instead of checking. The
command a client starts the server with stays plain: `@latest` asks PyPI on
every start.
"""

from __future__ import annotations

import re
from pathlib import Path

from opik_mcp.command_line import HELP_TEXT, TERMINAL_HINT

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every place that gives a person or an agent install commands to type.
INSTRUCTION_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "legacy" / "typescript" / "MIGRATION.md",
    REPO_ROOT / "scripts" / "skills_pack_readme.md.tmpl",
)

_ONE_OFF_WITHOUT_LATEST = re.compile(r"uvx (?:opik mcp \w+|opik-mcp --(?:help|check))")
_LAUNCH_WITH_A_VERSION = re.compile(r'-- uvx opik-mcp@|"opik-mcp@')


def _texts() -> dict[str, str]:
    return {
        "HELP_TEXT": HELP_TEXT,
        "TERMINAL_HINT": TERMINAL_HINT,
        **{str(path.relative_to(REPO_ROOT)): path.read_text() for path in INSTRUCTION_FILES},
    }


def test_one_off_commands_carry_latest() -> None:
    for name, text in (("HELP_TEXT", HELP_TEXT), ("README.md", _texts()["README.md"])):
        assert "uvx opik@latest mcp configure" in text, (
            f"{name} no longer gives the installer as `uvx opik@latest mcp configure`; "
            "the check below would pass with no one-off command left to check."
        )
    stale = {
        name: found
        for name, text in _texts().items()
        if (found := _ONE_OFF_WITHOUT_LATEST.findall(text))
    }
    assert not stale, (
        f"These run whatever copy uv cached first: {stale}. Write "
        "`uvx opik@latest mcp …` and `uvx opik-mcp@latest --help|--check`."
    )


def test_the_launch_command_stays_plain() -> None:
    for name, text in (("HELP_TEXT", HELP_TEXT), ("README.md", _texts()["README.md"])):
        assert "-- uvx opik-mcp" in text, (
            f"{name} no longer registers the server with `-- uvx opik-mcp`; the "
            "check below would pass with no launch command left to check."
        )
    versioned = {
        name: found
        for name, text in _texts().items()
        if (found := _LAUNCH_WITH_A_VERSION.findall(text))
    }
    assert not versioned, (
        f"A client would ask PyPI on every start: {versioned}. Launch with `uvx opik-mcp`."
    )

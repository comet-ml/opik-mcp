"""The plugin must pass the Claude plugin directory checks (OPIK-9351).

Comet is not a Verified organization, so the directory refuses three things in
`plugins/opik`: tools pre-approved beyond Read, Grep, Glob and LS; install
commands in any text; and samples that read an API key from the environment.
The directory scans `main` only, so a regression shows up after the merge.
These checks catch it before.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from skills_ref import read_properties

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "opik"
SKILLS_ROOT = PLUGIN_ROOT / "skills"

ALLOWED_TOOLS = {"Read", "Grep", "Glob", "LS"}

INSTALL_COMMAND = re.compile(
    r"\b(?:pip3?|uv|npm|pnpm|yarn|bun|poetry|conda|brew|cargo|go)\s+(?:install|add|i)\b"
    r"|\b(?:uvx|npx|pipx)\s"
    r"|\b(?:curl|wget)\b[^\n]*\|\s*(?:ba)?sh\b"
)
KEY_FROM_ENVIRONMENT = re.compile(
    r"(?:os\.environ(?:\.get)?\s*[\[(]\s*|os\.getenv\(\s*|process\.env\.|\$\{?)"
    r"[\"']?[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\b"
)


def _skill_dirs() -> list[Path]:
    return sorted(d for d in SKILLS_ROOT.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def _text_files() -> list[Path]:
    return sorted(p for p in PLUGIN_ROOT.rglob("*") if p.is_file() and p.suffix in {".md", ".json"})


def _matches(pattern: re.Pattern[str]) -> list[str]:
    hits = []
    for path in _text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(PLUGIN_ROOT)}:{number}: {line.strip()}")
    return hits


def test_files_are_discovered() -> None:
    assert _skill_dirs(), f"no skills under {SKILLS_ROOT}"
    assert _text_files(), f"no text files under {PLUGIN_ROOT}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=[d.name for d in _skill_dirs()])
def test_skill_pre_approves_only_read_tools(skill_dir: Path) -> None:
    allowed = read_properties(skill_dir).allowed_tools or ""
    tools = set(allowed.split()) if isinstance(allowed, str) else set(allowed)
    extra = tools - ALLOWED_TOOLS
    assert not extra, (
        f"{skill_dir.name}: allowed-tools {sorted(extra)}; the directory accepts only "
        f"{sorted(ALLOWED_TOOLS)} from an unverified organization"
    )


def test_no_install_commands() -> None:
    hits = _matches(INSTALL_COMMAND)
    assert not hits, (
        "the directory refuses install commands; name the package or link its docs:\n"
        + "\n".join(hits)
    )


def test_no_key_read_from_the_environment() -> None:
    hits = _matches(KEY_FROM_ENVIRONMENT)
    assert not hits, (
        "the directory refuses samples that read a key from the environment; "
        "use a placeholder such as YOUR_API_KEY:\n" + "\n".join(hits)
    )

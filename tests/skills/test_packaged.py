"""The shared skills must ship inside the `opik_mcp` package (OPIK-7471).

Skills live at `plugins/opik/skills/`, the canonical home for the Opik
coding-agent skills, and the wheel packs them as `opik_mcp/skills`. They are
served over MCP by `read_skill` (OPIK-7472), so they must be locatable at
runtime via package resources — not just present in the source tree.

This asserts the four shared skills and their `SKILL.md` entry points resolve
through `importlib.resources`, which is what proves they were packaged into the
wheel rather than left behind as untracked source files.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

import pytest

SHARED_SKILLS = ("opik", "opik-evaluate", "opik-instrument")


def _skills_root() -> Traversable:
    return files("opik_mcp") / "skills"


def test_skills_directory_is_a_package_resource() -> None:
    root = _skills_root()
    assert root.is_dir(), "opik_mcp/skills is not resolvable as a package resource"


@pytest.mark.parametrize("skill", SHARED_SKILLS)
def test_each_shared_skill_has_a_skill_md(skill: str) -> None:
    skill_md = _skills_root() / skill / "SKILL.md"
    assert skill_md.is_file(), f"skills/{skill}/SKILL.md is missing from the package"
    text = skill_md.read_text(encoding="utf-8")
    assert text.lstrip().startswith("---"), f"skills/{skill}/SKILL.md lacks frontmatter"
    assert f"name: {skill}" in text, f"skills/{skill}/SKILL.md name does not match its dir"

"""`opik-mcp skills` CLI — list + install onto disk without the MCP (OPIK-7592).

Exercises the offline front-door: `list` names the bundled skills, `install`
copies them into a target dir, unknown names are rejected, and an existing
skill is skipped unless `--force` is given.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opik_mcp.skills_install import _available, run

SHARED_SKILLS = ("opik", "agent-ops", "evaluation")


def test_available_lists_the_shared_skills() -> None:
    available = _available()
    for skill in SHARED_SKILLS:
        assert skill in available
    # the folder README is a file, not a skill directory
    assert "README" not in available and "README.md" not in available


def test_list_prints_skill_names(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["list"]) == 0
    out = capsys.readouterr().out
    for skill in SHARED_SKILLS:
        assert skill in out


def test_install_all_copies_skill_trees(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    assert run(["install", "--dir", str(dest)]) == 0
    for skill in SHARED_SKILLS:
        assert (dest / skill / "SKILL.md").is_file()
    # references travel with the skill
    assert (dest / "opik" / "references").is_dir()


def test_install_subset(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    assert run(["install", "--dir", str(dest), "opik"]) == 0
    assert (dest / "opik" / "SKILL.md").is_file()
    assert not (dest / "agent-ops").exists()


def test_install_unknown_skill_errors(tmp_path: Path) -> None:
    assert run(["install", "--dir", str(tmp_path / "s"), "does-not-exist"]) == 2


def test_install_skips_existing_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "skills"
    run(["install", "--dir", str(dest), "opik"])
    marker = dest / "opik" / "MARKER.txt"
    marker.write_text("keep", encoding="utf-8")

    assert run(["install", "--dir", str(dest), "opik"]) == 0
    assert "skip opik" in capsys.readouterr().out
    assert marker.exists()  # untouched — directory was not overwritten

    assert run(["install", "--dir", str(dest), "--force", "opik"]) == 0
    assert not marker.exists()  # --force replaced the tree

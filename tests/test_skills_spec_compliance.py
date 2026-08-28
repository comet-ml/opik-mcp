"""Every authored skill must satisfy the agentskills.io specification (OPIK-7621).

The skills are published as a public `npx skills add` pack and are read directly by
~40 coding agents. A skill whose frontmatter the standard rejects is not a style
nit: agents that use the reference parser drop the offending keys silently, so
provenance vanishes without an error anywhere.

Validation runs against `skills_ref` — the reference implementation of the spec —
rather than a hand-rolled check, so "valid" here means exactly what an agent's own
tooling means by it, with no second opinion to drift.

The pack generator copies skill files verbatim, so this suite is what keeps the
published pack compliant: there is no normalisation step downstream to fall back on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from skills_ref import read_properties, validate

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "src" / "opik_mcp" / "skills"


def _skill_dirs() -> list[Path]:
    """Every authored skill, discovered from the tree rather than hardcoded.

    Four more skills have open scaffolding PRs (OPIK-7648/7649/7650/7651); a
    hardcoded list would make each of them fail this suite on arrival.
    """
    return sorted(d for d in SKILLS_ROOT.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def _ids(dirs: list[Path]) -> list[str]:
    return [d.name for d in dirs]


def test_at_least_one_skill_is_discovered() -> None:
    """Guard the parametrisation itself.

    Every test below is parametrised over discovered directories, so a bad root
    path would silently collect zero cases and the suite would pass while
    checking nothing.
    """
    assert _skill_dirs(), f"no skills discovered under {SKILLS_ROOT}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=_ids(_skill_dirs()))
def test_skill_satisfies_the_reference_validator(skill_dir: Path) -> None:
    errors = validate(skill_dir)
    assert not errors, f"{skill_dir.name} fails the agentskills.io spec: {errors}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=_ids(_skill_dirs()))
def test_skill_name_matches_its_directory(skill_dir: Path) -> None:
    """The spec requires it, and the installer keys on the directory name."""
    assert read_properties(skill_dir).name == skill_dir.name


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=_ids(_skill_dirs()))
def test_skill_name_is_namespaced_to_opik(skill_dir: Path) -> None:
    """Skill names are a global namespace, and the installer resolves collisions
    by overwriting without a prompt or a backup.

    The product's onboarding runs `npx skills add comet-ml/opik-skills -g --all`.
    A skill published here as `evaluate` therefore destroys any other `evaluate`
    already on the user's machine — verified: the pre-existing directory is
    replaced outright, extra files in it are deleted, and the installer reports
    success. The reverse is equally true; the next pack to claim the name takes
    ours, and nothing surfaces the swap because the agent still finds *a* skill
    under the name it expects.

    `opik` itself is the product name and is ours to claim. Everything else must
    carry the prefix.
    """
    name = skill_dir.name
    assert name == "opik" or name.startswith("opik-"), (
        f"{name} claims an unnamespaced global skill name; call it opik-{name}"
    )


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=_ids(_skill_dirs()))
def test_provenance_survives_the_reference_reader(skill_dir: Path) -> None:
    """Provenance must live under `metadata`, where the spec can carry it.

    Declared as top-level keys it parses "fine" and is then discarded by every
    spec-compliant reader — the failure mode this asserts against. `source_commit`
    stays unresolved until OPIK-7800 stamps a verified release; that it is a
    placeholder is fine, that it is *reachable* is what matters here.
    """
    # `metadata` is None, not {}, when the mapping is absent entirely.
    metadata = read_properties(skill_dir).metadata or {}
    assert "last_updated" in metadata, f"{skill_dir.name} loses last_updated to the reader"
    assert "source_commit" in metadata, f"{skill_dir.name} loses source_commit to the reader"

"""Building the publishable skills pack (OPIK-7621).

The pack is what `npx skills add comet-ml/opik-skills` resolves to, and the product's
onboarding installs it globally with `--all`. So the contract under test is "what
lands on a user's disk", and every assertion here is about the emitted directory —
never about how the builder gets there.

Tests run against fixture trees, never against `src/opik_mcp/skills`. Four more
skills already have open scaffolding PRs (OPIK-7648/7649/7650/7651); a suite pinned
to today's set would fail on each arrival, and would stop telling us anything about
the builder.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from scripts.build_skills_pack import PackBuildError, build_pack

SKILL_MD = """\
---
name: {name}
description: {description}
metadata:
  last_updated: "2026-08-01"
---

# {name}

Body of {name}.
"""


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "Does a thing. Use when the user asks for the thing.",
    references: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    """Create one skill directory. `extra` takes paths relative to the skill root."""
    skill_dir = root / name
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_MD.format(name=name, description=description), encoding="utf-8"
    )
    for filename, body in (references or {}).items():
        (skill_dir / "references" / filename).write_text(body, encoding="utf-8")
    for relpath, body in (extra or {}).items():
        target = skill_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return skill_dir


def _files_under(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def src(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "opik", references={"tracing.md": "# Tracing\n"})
    _write_skill(root, "evaluate", description="Runs evals. Use for measuring quality.")
    return root


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "pack"


def test_every_skill_reaches_the_pack(src: Path, out: Path) -> None:
    build_pack(src, out)
    assert (out / "skills" / "opik" / "SKILL.md").is_file()
    assert (out / "skills" / "evaluate" / "SKILL.md").is_file()


def test_reference_documents_travel_with_their_skill(src: Path, out: Path) -> None:
    build_pack(src, out)
    assert (out / "skills" / "opik" / "references" / "tracing.md").read_text() == "# Tracing\n"


def test_skill_files_are_byte_identical_to_source(src: Path, out: Path) -> None:
    """Verbatim copying is what makes divergence questions answerable.

    There is no normalisation step downstream, so if the published bytes could
    differ from the authored bytes, "did this drift?" would have no crisp answer.
    """
    build_pack(src, out)
    for authored in sorted(src.rglob("*.md")):
        published = out / "skills" / authored.relative_to(src)
        assert published.read_bytes() == authored.read_bytes(), authored


def test_optional_spec_directories_are_published(src: Path, out: Path) -> None:
    """`scripts/` and `assets/` are blessed by the spec; authors may add them."""
    _write_skill(
        src,
        "instrument",
        extra={"scripts/detect.py": "print('hi')\n", "assets/template.txt": "x\n"},
    )
    build_pack(src, out)
    assert (out / "skills" / "instrument" / "scripts" / "detect.py").is_file()
    assert (out / "skills" / "instrument" / "assets" / "template.txt").is_file()


def test_evaluation_fixtures_do_not_ship(src: Path, out: Path) -> None:
    """Fixtures are development artifacts and the pack installs globally.

    OPIK-7800 builds fixture repositories under `evals/`; shipping them would
    push megabytes no agent reads onto every user's machine.
    """
    _write_skill(src, "instrument", extra={"evals/fixtures/app/main.py": "x = 1\n"})
    build_pack(src, out)
    assert (out / "skills" / "instrument" / "SKILL.md").is_file()
    assert not (out / "skills" / "instrument" / "evals").exists()


def test_dotfiles_do_not_ship(src: Path, out: Path) -> None:
    _write_skill(src, "instrument", extra={".DS_Store": "junk\n", ".notes/scratch.md": "x\n"})
    build_pack(src, out)
    assert not (out / "skills" / "instrument" / ".DS_Store").exists()
    assert not (out / "skills" / "instrument" / ".notes").exists()


def test_a_directory_without_a_skill_md_is_not_a_skill(src: Path, out: Path) -> None:
    (src / "shared-notes").mkdir()
    (src / "shared-notes" / "draft.md").write_text("wip\n", encoding="utf-8")
    manifest = build_pack(src, out)
    assert [s["name"] for s in manifest["skills"]] == ["evaluate", "opik"]
    assert not (out / "skills" / "shared-notes").exists()


def test_manifest_lists_every_skill_with_its_description(src: Path, out: Path) -> None:
    manifest = build_pack(src, out)
    by_name = {s["name"]: s for s in manifest["skills"]}
    assert set(by_name) == {"opik", "evaluate"}
    assert by_name["evaluate"]["description"].startswith("Runs evals.")


def test_manifest_lists_the_files_that_were_emitted(src: Path, out: Path) -> None:
    manifest = build_pack(src, out)
    opik = next(s for s in manifest["skills"] if s["name"] == "opik")
    assert [f["path"] for f in opik["files"]] == ["SKILL.md", "references/tracing.md"]


def test_manifest_checksums_match_the_emitted_bytes(src: Path, out: Path) -> None:
    manifest = build_pack(src, out)
    for skill in manifest["skills"]:
        for entry in skill["files"]:
            published = out / "skills" / skill["name"] / entry["path"]
            digest = hashlib.sha256(published.read_bytes()).hexdigest()
            assert entry["sha256"] == digest, f"{skill['name']}/{entry['path']}"


def test_manifest_records_the_revision_it_was_built_from(src: Path, out: Path) -> None:
    """A user's installed pack must be traceable to a commit."""
    manifest = build_pack(src, out, pack_version="1.2.3", source_commit="abc1234")
    assert manifest["source_commit"] == "abc1234"
    assert manifest["pack_version"] == "1.2.3"
    assert manifest["schema_version"] == 1


@pytest.mark.parametrize(
    ("field", "values"),
    [("pack_version", ("1.2.3", "9.9.9")), ("source_commit", ("aaaaaaa", "bbbbbbb"))],
)
def test_content_digest_ignores_build_identity(
    src: Path, out: Path, field: str, values: tuple[str, str]
) -> None:
    """The consumer compares this digest to decide whether to raise a PR.

    Folding either value into it would make every merge look like a content
    change, so the sync would open a pull request with an empty diff.
    """
    first = build_pack(src, out, **{field: values[0]})
    second = build_pack(src, out, **{field: values[1]})
    assert first["content_digest"] == second["content_digest"]
    assert first[field] != second[field]


def test_content_digest_changes_when_a_skill_changes(src: Path, out: Path) -> None:
    before = build_pack(src, out)["content_digest"]
    (src / "opik" / "references" / "tracing.md").write_text("# Tracing v2\n", encoding="utf-8")
    assert build_pack(src, out)["content_digest"] != before


def test_readme_lists_every_skill_with_its_description(src: Path, out: Path) -> None:
    build_pack(src, out)
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "`opik`" in readme
    assert "`evaluate`" in readme
    assert "Runs evals." in readme


def test_readme_carries_the_install_command_the_product_shows(src: Path, out: Path) -> None:
    """The product UI hardcodes this exact string; the README must not contradict it."""
    build_pack(src, out)
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "npx skills add comet-ml/opik-skills -g --all" in readme


def test_readme_warns_that_the_content_is_generated(src: Path, out: Path) -> None:
    build_pack(src, out)
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "generated" in readme.lower()
    assert "comet-ml/opik-mcp" in readme


def test_readme_does_not_carry_retired_material(src: Path, out: Path) -> None:
    """`AgentConfig` was retired in May; the hand-written README still sells it."""
    build_pack(src, out)
    assert "AgentConfig" not in (out / "README.md").read_text(encoding="utf-8")


def test_readme_tree_does_not_churn_on_reference_renames(src: Path, out: Path) -> None:
    """A rename must not produce README noise in the diff a human reviews each sync."""
    build_pack(src, out)
    before = (out / "README.md").read_text(encoding="utf-8")
    (src / "opik" / "references" / "tracing.md").rename(src / "opik" / "references" / "trace.md")
    build_pack(src, out)
    assert (out / "README.md").read_text(encoding="utf-8") == before


def test_adding_a_skill_changes_the_readme_and_manifest_and_nothing_else(
    src: Path, out: Path
) -> None:
    build_pack(src, out)
    generated = {"README.md", "index.json"}
    before = {
        p.relative_to(out): p.read_bytes()
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name not in generated
    }

    _write_skill(src, "instrument", description="Adds tracing. Use to instrument an app.")
    build_pack(src, out)

    after = {
        p.relative_to(out): p.read_bytes()
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name not in generated
    }
    added = set(after) - set(before)
    assert all(str(p).startswith("skills/instrument/") for p in added), added
    assert {p: b for p, b in after.items() if p in before} == before
    assert "instrument" in (out / "README.md").read_text(encoding="utf-8")


def test_invalid_frontmatter_fails_the_build(src: Path, out: Path) -> None:
    """A malformed pack must never be produced; the build stops instead."""
    (src / "opik" / "SKILL.md").write_text(
        "---\nname: opik\ndescription: x\nbogus: y\n---\n\nbody\n", encoding="utf-8"
    )
    with pytest.raises(PackBuildError, match="opik"):
        build_pack(src, out)


def test_name_not_matching_the_directory_fails_the_build(src: Path, out: Path) -> None:
    """The installer keys on the directory name; a mismatch misroutes the skill."""
    (src / "opik" / "SKILL.md").write_text(
        "---\nname: something-else\ndescription: x\n---\n\nbody\n", encoding="utf-8"
    )
    with pytest.raises(PackBuildError, match="opik"):
        build_pack(src, out)


def test_a_source_without_skills_fails_the_build(tmp_path: Path, out: Path) -> None:
    """An empty pack, published, would delete every skill from the public repo."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PackBuildError, match="no skills"):
        build_pack(empty, out)


def test_every_emitted_skill_is_validated_not_only_the_source(src: Path, out: Path) -> None:
    """Exclusion runs between reading and emitting, so the output is checked too."""
    build_pack(src, out)
    from skills_ref import validate  # local import: only this test needs it

    for skill in ("opik", "evaluate"):
        assert validate(out / "skills" / skill) == []


def test_cross_skill_references_are_allowed_when_they_resolve(src: Path, out: Path) -> None:
    """`/opik-instrument` reads `../opik/references/*.md`; siblings in the pack make that work.

    Verified against the real installer too: with `-g --all`, the command the product
    shows, the skills land as siblings and these paths resolve.
    """
    (src / "opik" / "references" / "integrations.md").write_text("# Integrations\n")
    _write_skill(src, "instrument")
    (src / "instrument" / "SKILL.md").write_text(
        SKILL_MD.format(name="instrument", description="Adds tracing. Use to instrument.")
        + "\nRead `../opik/references/integrations.md` for the full list.\n",
        encoding="utf-8",
    )
    build_pack(src, out)
    assert (out / "skills" / "instrument" / "SKILL.md").is_file()


def test_a_dangling_reference_fails_the_build(src: Path, out: Path) -> None:
    """A typo used to fail silently: the agent finds nothing, falls back to its own
    memory, and still reports success. Failing the build is cheaper."""
    (src / "opik" / "SKILL.md").write_text(
        SKILL_MD.format(name="opik", description="Reference. Use for SDK questions.")
        + "\nSee `references/tracing-pythn.md` for details.\n",
        encoding="utf-8",
    )
    with pytest.raises(PackBuildError, match=re.escape("tracing-pythn.md")):
        build_pack(src, out)


def test_a_reference_escaping_the_pack_fails_the_build(src: Path, out: Path) -> None:
    (src / "opik" / "SKILL.md").write_text(
        SKILL_MD.format(name="opik", description="Reference. Use for SDK questions.")
        + "\nSee [notes](../../../secrets.md).\n",
        encoding="utf-8",
    )
    with pytest.raises(PackBuildError, match="escapes the pack"):
        build_pack(src, out)


def test_urls_and_anchors_are_not_treated_as_paths(src: Path, out: Path) -> None:
    (src / "opik" / "SKILL.md").write_text(
        SKILL_MD.format(name="opik", description="Reference. Use for SDK questions.")
        + "\nSee [docs](https://www.comet.com/docs/opik/x.md) and [above](#core-concepts).\n",
        encoding="utf-8",
    )
    build_pack(src, out)  # must not raise


def test_identical_input_produces_identical_output(src: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    build_pack(src, first)
    build_pack(src, second)
    assert _files_under(first) == _files_under(second)
    for rel in sorted(_files_under(first)):
        assert (first / rel).read_bytes() == (second / rel).read_bytes(), rel


def test_rebuilding_over_a_previous_pack_drops_removed_skills(src: Path, out: Path) -> None:
    """Stale output would be published as if it were current."""
    _write_skill(src, "doomed")
    build_pack(src, out)
    assert (out / "skills" / "doomed").exists()

    shutil_rmtree_equivalent = sorted((src / "doomed").rglob("*"), reverse=True)
    for path in shutil_rmtree_equivalent:
        path.unlink() if path.is_file() else path.rmdir()
    (src / "doomed").rmdir()

    build_pack(src, out)
    assert not (out / "skills" / "doomed").exists()

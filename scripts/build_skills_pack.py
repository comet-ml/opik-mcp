"""Build the publishable `opik-skills` pack from the authored skills (OPIK-7621).

`opik-mcp` is where the Opik agent skills are authored. `comet-ml/opik-skills` is the
public pack that `npx skills add comet-ml/opik-skills` resolves to — a string hardcoded
in Opik's onboarding UI, so it cannot move. This script is the bridge: it turns the
authored skills into a standards-compliant pack that is published as an artifact and
pulled by the public repository.

Three properties matter, and the tests in `tests/test_skills_pack_build.py` pin them:

*Verbatim.* Skill files are copied byte for byte. Frontmatter is made spec-compliant at
the source (see `tests/test_skills_spec_compliance.py`), never rewritten here, so
"published equals authored" is literally true and drift questions have a crisp answer.
The offline export path (OPIK-7592) copies from the installed package and never runs
this script, so normalising here would fix one distribution path and leave the other.

*Deterministic.* The same input yields byte-identical output. The consumer compares
`content_digest` to decide whether to raise a pull request, so nondeterminism would
produce pull requests with empty diffs — and quickly teach everyone to ignore them.

*Fail loudly.* An invalid or empty source aborts the build, and every emitted skill is
re-validated after copying. The consumer replaces its skill tree with whatever it is
handed, so a silently empty or malformed pack would break the public pack.

Frontmatter is read through `skills_ref`, the reference implementation of the
agentskills.io spec, rather than a parser of our own — so this script's reading of a
skill is identical to the reading an agent's own tooling performs, with no second
implementation to drift. One current skill declares its description as a folded scalar
across six lines, exactly the construct hand-rolled parsers mishandle.

Usage:
    python scripts/build_skills_pack.py [--src DIR] [--out DIR]
                                        [--pack-version V] [--source-commit SHA]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skills_ref import SkillProperties, read_properties, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO_ROOT / "src" / "opik_mcp" / "skills"
DEFAULT_OUT = REPO_ROOT / "dist" / "opik-skills"
README_TEMPLATE = Path(__file__).resolve().parent / "skills_pack_readme.md.tmpl"

SOURCE_REPO = "comet-ml/opik-mcp"
MANIFEST_SCHEMA_VERSION = 1
UNVERSIONED = "0.0.0.dev0"
UNKNOWN_COMMIT = "unknown"

#: Directory names never published. `evals/` holds fixture repositories and harnesses
#: (OPIK-7800) that no agent reads; the product installs this pack globally with
#: `--all`, so shipping them would push megabytes onto every user's machine.
EXCLUDED_DIRS = frozenset({"evals", "__pycache__"})

#: Relative paths to Markdown a SKILL.md tells the agent to read, in the two forms
#: the skills use: a Markdown link, and a bare backticked path in prose. Both are
#: instructions to open a file, so both have to resolve.
_MARKDOWN_LINK = re.compile(r"\]\(\s*(?!<)([^)\s]+\.md)\s*\)")
_BACKTICKED_PATH = re.compile(r"`([^`\s]+\.md)`")


class PackBuildError(RuntimeError):
    """The source tree cannot produce a publishable pack."""


@dataclass(frozen=True)
class PackedFile:
    path: str
    sha256: str

    def as_json(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class PackedSkill:
    name: str
    description: str
    files: tuple[PackedFile, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "files": [f.as_json() for f in self.files],
        }


def _is_publishable(path: Path, skill_dir: Path) -> bool:
    """Exclude by rule, not by allow-list.

    An allow-list would silently drop an author's new `scripts/` or `assets/`
    directory — both blessed by the spec — and they would never find out.
    """
    relative = path.relative_to(skill_dir)
    return not any(part in EXCLUDED_DIRS or part.startswith(".") for part in relative.parts)


def _discover_skills(src_root: Path) -> list[Path]:
    """Every directory holding a SKILL.md, sorted for determinism.

    A directory without a SKILL.md is not a skill — shared notes and scratch
    directories must not become one by sitting in the right place.
    """
    if not src_root.is_dir():
        raise PackBuildError(f"source directory does not exist: {src_root}")
    return sorted(d for d in src_root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def _read_valid_skill(skill_dir: Path) -> SkillProperties:
    """Validate against the spec and confirm the declared name matches the directory."""
    errors = validate(skill_dir)
    if errors:
        raise PackBuildError(f"{skill_dir.name} fails the agentskills.io spec: {errors}")

    properties: SkillProperties = read_properties(skill_dir)
    if properties.name != skill_dir.name:
        # The installer keys on the directory name; a mismatch installs the skill
        # under a name nothing references.
        raise PackBuildError(
            f"{skill_dir.name} declares name {properties.name!r}, which does not match "
            "its directory"
        )
    return properties


def _copy_skill(skill_dir: Path, target: Path) -> tuple[PackedFile, ...]:
    """Copy one skill verbatim, returning its manifest file entries."""
    entries: list[PackedFile] = []
    for source in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        if not _is_publishable(source, skill_dir):
            continue
        relative = source.relative_to(skill_dir)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        destination.write_bytes(payload)
        entries.append(
            PackedFile(path=relative.as_posix(), sha256=hashlib.sha256(payload).hexdigest())
        )
    # SKILL.md first — it is the entry point, and Stripe's public manifest orders it so.
    entries.sort(key=lambda e: (e.path != "SKILL.md", e.path))
    return tuple(entries)


def _referenced_markdown(text: str) -> set[str]:
    """Relative Markdown paths a SKILL.md instructs the agent to read."""
    found = set(_MARKDOWN_LINK.findall(text)) | set(_BACKTICKED_PATH.findall(text))
    return {
        path
        for path in found
        # Absolute paths, URLs and anchors are not ours to resolve.
        if "://" not in path and not path.startswith(("/", "#"))
    }


def _check_references_resolve(skills_root: Path) -> None:
    """Every path a packed SKILL.md points at must exist inside the pack.

    Skills reference each other — `/instrument` reads `../opik/references/*.md` —
    so resolution is checked against the whole pack, not one skill directory.
    That also means a typo in such a path used to fail *silently*: the agent
    would find nothing, fall back to its own memory, and still report success.
    Cheaper to fail the build.
    """
    dangling: list[str] = []
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        for reference in sorted(_referenced_markdown(skill_md.read_text(encoding="utf-8"))):
            target = (skill_dir / reference).resolve()
            inside_pack = target.is_relative_to(skills_root.resolve())
            if not inside_pack or not target.is_file():
                reason = "escapes the pack" if not inside_pack else "does not exist"
                dangling.append(f"{skill_dir.name}/SKILL.md -> {reference} ({reason})")

    if dangling:
        raise PackBuildError("unresolvable references in the pack: " + "; ".join(dangling))


def _render_skills_table(skills: list[PackedSkill]) -> str:
    rows = [
        f"| [`{s.name}`](./skills/{s.name}/SKILL.md) | {s.description.strip()} |" for s in skills
    ]
    return "\n".join(["| Skill | What it does |", "| --- | --- |", *rows])


def _render_repository_tree(skills: list[PackedSkill]) -> str:
    """Skills only, deliberately.

    Listing every reference document would make the README churn on any rename,
    which is noise in a diff a human is asked to review on each sync.
    """
    lines = ["opik-skills/", "├── skills/"]
    for index, skill in enumerate(skills):
        lines.append(f"│   {'└──' if index == len(skills) - 1 else '├──'} {skill.name}/")
    lines += ["├── README.md", "├── index.json", "└── LICENSE"]
    return "\n".join(lines)


def _render_readme(skills: list[PackedSkill]) -> str:
    template = README_TEMPLATE.read_text(encoding="utf-8")
    return template.replace("{{SKILLS_TABLE}}", _render_skills_table(skills)).replace(
        "{{REPOSITORY_TREE}}", _render_repository_tree(skills)
    )


def _content_digest(skills: list[PackedSkill], readme: str) -> str:
    """A digest over what the consumer actually writes — skills and README.

    Deliberately excludes `pack_version` and `source_commit`: every merge produces
    new values for both, and folding them in would make each merge look like a
    content change, so the sync would raise a pull request with an empty diff.
    """
    hasher = hashlib.sha256()
    for skill in skills:
        for entry in skill.files:
            hasher.update(f"{skill.name}/{entry.path}\0{entry.sha256}\0".encode())
    hasher.update(hashlib.sha256(readme.encode("utf-8")).hexdigest().encode())
    return hasher.hexdigest()


def build_pack(
    src_root: Path,
    out_root: Path,
    *,
    pack_version: str = UNVERSIONED,
    source_commit: str = UNKNOWN_COMMIT,
) -> dict[str, Any]:
    """Build the pack at `out_root` from the skills in `src_root`; return the manifest.

    `out_root` is replaced, not merged: a skill deleted upstream must disappear here,
    or stale content would be published as though it were current.
    """
    skill_dirs = _discover_skills(src_root)
    if not skill_dirs:
        raise PackBuildError(f"no skills found under {src_root} — refusing to build an empty pack")

    properties = {d: _read_valid_skill(d) for d in skill_dirs}

    if out_root.exists():
        shutil.rmtree(out_root)
    (out_root / "skills").mkdir(parents=True)

    skills: list[PackedSkill] = []
    for skill_dir in skill_dirs:
        target = out_root / "skills" / skill_dir.name
        files = _copy_skill(skill_dir, target)
        # Re-validate what was emitted, not only what was read: the exclusion rules
        # run between the two, and dropping a file the skill depends on must not be
        # something a user discovers instead of CI.
        emitted_errors = validate(target)
        if emitted_errors:
            raise PackBuildError(f"packed {skill_dir.name} fails the spec: {emitted_errors}")
        skills.append(
            PackedSkill(
                name=properties[skill_dir].name,
                description=properties[skill_dir].description,
                files=files,
            )
        )

    # After every skill is in place: cross-skill references can only be checked
    # once the whole pack exists.
    _check_references_resolve(out_root / "skills")

    readme = _render_readme(skills)
    (out_root / "README.md").write_text(readme, encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pack_version": pack_version,
        "source": SOURCE_REPO,
        "source_commit": source_commit,
        "content_digest": _content_digest(skills, readme),
        "skills": [s.as_json() for s in skills],
    }
    (out_root / "index.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the publishable opik-skills pack.")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--pack-version",
        default=UNVERSIONED,
        help="informational only; excluded from content_digest",
    )
    parser.add_argument(
        "--source-commit",
        default=UNKNOWN_COMMIT,
        help="revision this pack was built from; excluded from content_digest",
    )
    args = parser.parse_args(argv)

    try:
        manifest = build_pack(
            args.src,
            args.out,
            pack_version=args.pack_version,
            source_commit=args.source_commit,
        )
    except PackBuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    names = ", ".join(s["name"] for s in manifest["skills"])
    print(f"built {len(manifest['skills'])} skills ({names}) -> {args.out}")
    print(f"content_digest: {manifest['content_digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

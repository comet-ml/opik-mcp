"""Every design doc has the agreed shape and a row in the index.

`docs/<feature>/design-doc.md` is the spec the `understand` agent reads first.
The six sections are fixed so a reader finds the same thing in the same place
in every doc, and the index in `docs/README.md` is where a reader picks the doc
by the question they hold. A doc missing from the index is a doc nobody opens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
INDEX = DOCS_DIR / "README.md"

SECTIONS = [
    "Purpose",
    "What it does now",
    "How it works",
    "Decisions",
    "Proven by",
    "Log",
]

DESIGN_DOCS = sorted(DOCS_DIR.glob("*/design-doc.md"))
FEATURES = [path.parent.name for path in DESIGN_DOCS]


def _h2_headings(text: str) -> list[str]:
    return re.findall(r"^## (.+?)\s*$", text, re.MULTILINE)


def _index_links() -> list[str]:
    return re.findall(r"\]\(([\w-]+)/design-doc\.md\)", INDEX.read_text())


def test_there_are_design_docs_to_check() -> None:
    assert DESIGN_DOCS, "no docs/<feature>/design-doc.md found; the index has nothing to point at"


@pytest.mark.parametrize("path", DESIGN_DOCS, ids=FEATURES)
def test_a_design_doc_has_the_six_sections_in_order(path: Path) -> None:
    headings = _h2_headings(path.read_text())
    assert headings == SECTIONS, (
        f"{path.relative_to(REPO_ROOT)} has H2 sections {headings}; "
        f"the fixed skeleton is SECTIONS in tests/repo/test_design_docs.py: {SECTIONS}"
    )


@pytest.mark.parametrize("path", DESIGN_DOCS, ids=FEATURES)
def test_a_design_doc_opens_with_its_feature_as_the_title(path: Path) -> None:
    first_line = path.read_text().splitlines()[0]
    assert first_line == f"# {path.parent.name}", (
        f"{path.relative_to(REPO_ROOT)} starts with {first_line!r}; "
        f"the title is the folder name so the index and the doc agree"
    )


@pytest.mark.parametrize("feature", FEATURES)
def test_every_design_doc_has_a_row_in_the_index(feature: str) -> None:
    assert feature in _index_links(), (
        f"docs/{feature}/design-doc.md exists but docs/README.md does not link it; "
        f"every design doc needs a row there (feature, the question it answers, the paths it owns)"
    )


@pytest.mark.parametrize("feature", sorted(set(_index_links())))
def test_every_index_row_points_at_a_design_doc(feature: str) -> None:
    assert (DOCS_DIR / feature / "design-doc.md").exists(), (
        f"docs/README.md links docs/{feature}/design-doc.md, which does not exist; "
        f"add the doc or remove the row, since every index row must open a design doc"
    )


LINK = re.compile(r"\]\(((?!https?:|mailto:)[^)\s#]*\.md)?(?:#([^)\s]+))?\)")


def _slug(heading: str) -> str:
    # GitHub's anchor rule: lowercase, drop punctuation except hyphens and
    # spaces, then spaces become hyphens.
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {_slug(h) for h in re.findall(r"^#{1,6} (.+?)\s*$", path.read_text(), re.MULTILINE)}


def _doc_links() -> list[tuple[str, str, str]]:
    links = []
    for doc in sorted(DOCS_DIR.rglob("*.md")):
        for target, anchor in LINK.findall(doc.read_text()):
            if target or anchor:
                links.append((doc.relative_to(REPO_ROOT).as_posix(), target, anchor))
    return links


@pytest.mark.parametrize(("doc", "target", "anchor"), _doc_links())
def test_a_link_between_docs_opens_an_existing_file_and_heading(
    doc: str, target: str, anchor: str
) -> None:
    source = REPO_ROOT / doc
    path = (source.parent / target).resolve() if target else source
    assert path.exists(), f"{doc} links {target}, which does not exist"
    if anchor:
        assert anchor in _anchors(path), (
            f"{doc} links {target}#{anchor}, but {path.relative_to(REPO_ROOT)} has no heading "
            f"with that anchor; link a heading that exists or drop the anchor"
        )

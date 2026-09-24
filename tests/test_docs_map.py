# Every test file, test name and source path that the agent docs cite must
# exist. Those docs are the map agents follow; a renamed test would otherwise
# leave them pointing at nothing. A test name counts if any test file defines
# it, so a doc can name a test without its file.

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DOCS = sorted(
    {
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CLAUDE.md",
        *(REPO_ROOT / ".claude").glob("rules/*.md"),
        *(REPO_ROOT / ".claude").glob("agents/*.md"),
        *(REPO_ROOT / ".claude").glob("commands/*.md"),
        *(REPO_ROOT / "docs").rglob("*.md"),
        *(REPO_ROOT / ".claude" / "dogfood" / "memory").glob("*.md"),
    }
)

PATH = re.compile(r"(?<![\w/.])((?:tests|src|scripts)/[\w./-]+\.py)\b")
TEST_FILE = re.compile(r"(?<![\w/.])(test_\w+\.py)\b")
TEST_NAME = re.compile(r"`(test_\w+)`")


@functools.cache
def _test_files() -> frozenset[str]:
    return frozenset(path.name for path in (REPO_ROOT / "tests").rglob("test_*.py"))


@functools.cache
def _test_names() -> frozenset[str]:
    names: set[str] = set()
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(node.name)
    return frozenset(names)


def _citations(pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    return sorted(
        {
            (doc.relative_to(REPO_ROOT).as_posix(), match)
            for doc in DOCS
            for match in pattern.findall(doc.read_text())
        }
    )


@pytest.mark.parametrize("pattern", [PATH, TEST_FILE, TEST_NAME])
def test_the_map_cites_something(pattern: re.Pattern[str]) -> None:
    assert _citations(pattern), f"{pattern.pattern} stopped matching the docs"


@pytest.mark.parametrize(("doc", "path"), _citations(PATH))
def test_cited_path_exists(doc: str, path: str) -> None:
    assert (REPO_ROOT / path).exists(), f"{doc} cites {path}, which does not exist"


@pytest.mark.parametrize(("doc", "name"), _citations(TEST_FILE))
def test_cited_test_file_exists(doc: str, name: str) -> None:
    assert name in _test_files(), f"{doc} cites {name}, which no folder under tests/ holds"


@pytest.mark.parametrize(("doc", "name"), _citations(TEST_NAME))
def test_cited_test_exists(doc: str, name: str) -> None:
    assert name in _test_names(), f"{doc} cites {name}, which no test defines"


def _rule_globs() -> list[tuple[str, str]]:
    globs: list[tuple[str, str]] = []
    for rule in sorted((REPO_ROOT / ".claude" / "rules").glob("*.md")):
        head = rule.read_text().split("---")[1] if rule.read_text().startswith("---") else ""
        globs += [(rule.name, g) for g in re.findall(r'^\s*-\s*"([^"]+)"', head, re.MULTILINE)]
    return globs


@pytest.mark.parametrize(("rule", "glob"), _rule_globs())
def test_rule_paths_match_a_file(rule: str, glob: str) -> None:
    # A rule scoped to a path that no longer exists silently stops loading.
    assert any(REPO_ROOT.glob(glob)), f"{rule} is scoped to {glob}, which matches no file"

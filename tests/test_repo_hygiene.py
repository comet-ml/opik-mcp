"""What the repository tracks and what the default test run selects.

`docs/` holds only product-engineering specs: the index, one design doc per
feature and the ADRs. Plans, grilling notes and briefs stay on disk and never
reach a PR, because the repository is public. Local tooling folders stay out
for the same reason. These are asserted through `git check-ignore`, so the test
checks what git does rather than what the ignore file says.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# Private, but it is the parser pytest uses for -m, so the test reads addopts
# exactly as pytest will.
from _pytest.mark.expression import Expression

REPO_ROOT = Path(__file__).resolve().parent.parent

TRACKED = [
    "docs/README.md",
    "docs/decisions/README.md",
    "docs/decisions/0001-context-budget-first.md",
    "docs/tool-surface/design-doc.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/rules/python.md",
    ".claude/agents/code-reviewer.md",
    ".claude/commands/dogfood.md",
    ".claude/settings.json",
    ".claude/hooks/protect_paths.py",
    ".claude/dogfood/memory/some-entry.md",
]

IGNORED = [
    "docs/team-brief.md",
    "docs/opik-mcp-demo.mp4",
    "docs/plans/some-plan.md",
    "docs/tool-surface/plans/some-plan.md",
    "docs/tool-surface/notes.md",
    "docs/superpowers/specs/some-spec.md",
    "docs/history/design.md",
    "demo/seed_demo.py",
    "presentation/index.html",
    ".claude/worktrees/some-branch/README.md",
    ".claude/settings.local.json",
    ".claude/skills/some-skill/SKILL.md",
    ".agents/skills/some-skill/SKILL.md",
]


def _is_ignored(path: str) -> bool:
    # --no-index: judge the rules alone, not whether the path is already tracked.
    # The repo's own rules only: a developer's global ignore file would hide
    # or excuse paths differently on each machine.
    result = subprocess.run(
        ["git", "-c", "core.excludesFile=", "check-ignore", "--no-index", "--quiet", path],
        cwd=REPO_ROOT,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull},
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize("path", TRACKED)
def test_path_is_trackable(path: str) -> None:
    assert not _is_ignored(path), f"{path} is ignored, but it belongs in the repository"


@pytest.mark.parametrize("path", IGNORED)
def test_path_is_ignored(path: str) -> None:
    assert _is_ignored(path), f"{path} would be committed, but it must stay local"


@pytest.mark.parametrize("marker", ["hermetic", "live", "user_flows"])
def test_marker_is_registered_and_off_by_default(marker: str, pytestconfig: pytest.Config) -> None:
    registered = {line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")}
    assert marker in registered

    # Already split the way pytest reads it, quotes included.
    addopts: list[str] = pytestconfig.getini("addopts")
    assert "-m" in addopts, "addopts no longer deselects the slow suites"
    expression = addopts[addopts.index("-m") + 1]

    def only_this_marker(name: str, /, **_: str | int | bool | None) -> bool:
        return name == marker

    selected = Expression.compile(expression).evaluate(only_this_marker)
    assert not selected, f"a test marked {marker} would run in the default suite"


@pytest.mark.parametrize(
    ("target", "suite", "ticket"),
    [("live", "tests/live", "OPIK-8490"), ("user-flows", "tests/user_flows", "OPIK-8491")],
)
def test_a_missing_suite_says_so_and_passes(target: str, suite: str, ticket: str) -> None:
    if (REPO_ROOT / suite).exists():
        pytest.skip(f"{suite} exists; the target runs it")
    result = subprocess.run(
        ["make", "-s", target], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, "an absent suite must not fail the run"
    assert f"not present yet ({ticket})" in result.stdout

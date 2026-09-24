# Everything that ratchets only shrinks. tests/ratchets.json is the record:
# the lint baselines in pyproject.toml must match it exactly, suppressions may
# not appear in new files or grow, and an entry that no longer matches a
# finding is debt already paid, so it has to go. A local test can't stop an
# edit to both files; that growth is for the reviewer to refuse.

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
RATCHETS = json.loads((REPO_ROOT / "tests" / "ratchets.json").read_text())
POLICY_GLOBS = {"scripts/**", ".claude/hooks/**", "tests/**"}
SUPPRESSION = re.compile(r"#\s*(?:noqa|type:\s*ignore)")

_POLICY = {"src/opik_mcp/_version.py"}
RUFF_BASELINE: dict[str, list[str]] = {
    path: codes
    for path, codes in CONFIG["tool"]["ruff"]["lint"]["per-file-ignores"].items()
    if "*" not in path and path not in _POLICY
}
MYPY_BASELINE: list[str] = next(
    override["module"]
    for override in CONFIG["tool"]["mypy"]["overrides"]
    if isinstance(override["module"], list)
    and override.get("disallow_any_explicit") is False
    and "opik_mcp.opik_client" not in override["module"]
)


def test_the_baselines_are_found() -> None:
    assert RUFF_BASELINE, "the ruff baseline stopped parsing"
    assert MYPY_BASELINE, "the mypy baseline stopped parsing"


def test_the_ruff_baseline_matches_the_record() -> None:
    assert RATCHETS["ruff_baseline"] == RUFF_BASELINE, (
        "pyproject.toml's ruff baseline and tests/ratchets.json differ. "
        "Entries only get deleted, from both."
    )


def test_the_mypy_baseline_matches_the_record() -> None:
    assert sorted(MYPY_BASELINE) == RATCHETS["mypy_baseline"], (
        "pyproject.toml's mypy baseline and tests/ratchets.json differ. "
        "Entries only get deleted, from both."
    )


def test_no_new_glob_exemptions() -> None:
    globs = {key for key in CONFIG["tool"]["ruff"]["lint"]["per-file-ignores"] if "*" in key}
    assert globs == POLICY_GLOBS, f"a glob exemption excuses files nobody listed: {globs}"


def test_suppressions_only_shrink() -> None:
    found: dict[str, int] = {}
    for root in ("src", "tests", "scripts"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "/evals/" in path.as_posix():
                continue
            count = len(SUPPRESSION.findall(path.read_text()))
            if count:
                found[path.relative_to(REPO_ROOT).as_posix()] = count
    assert found == RATCHETS["suppressions"], (
        "noqa / type: ignore counts differ from tests/ratchets.json. Fix the finding "
        "instead of suppressing it; after removing one, lower its count there."
    )


def test_every_ruff_entry_still_has_its_findings() -> None:
    codes = sorted({code for listed in RUFF_BASELINE.values() for code in listed})
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "ruff",
            "check",
            *RUFF_BASELINE,
            "--select",
            ",".join(codes),
            "--config",
            "lint.per-file-ignores = {}",
            "--output-format",
            "json",
            "--no-cache",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip(), f"ruff did not run as expected:\n{result.stderr}"
    found: dict[str, set[str]] = {}
    for finding in json.loads(result.stdout):
        path = Path(finding["filename"]).relative_to(REPO_ROOT).as_posix()
        found.setdefault(path, set()).add(finding["code"])
    stale = {
        path: sorted(set(listed) - found.get(path, set()))
        for path, listed in RUFF_BASELINE.items()
        if set(listed) - found.get(path, set())
    }
    assert not stale, f"fixed but still excused in pyproject.toml, remove them: {stale}"


def test_every_mypy_entry_still_uses_any(tmp_path: Path) -> None:
    # mypy with the baseline override dropped: every listed module must still
    # report explicit Any, or it has been typed and its line has to go.
    config = (REPO_ROOT / "pyproject.toml").read_text()
    start = config.index("# Baseline: modules that used explicit Any")
    end = config.index("disallow_any_explicit = false", start) + len(
        "disallow_any_explicit = false"
    )
    strict = tmp_path / "pyproject.toml"
    strict.write_text(config[:start] + config[end:])
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "mypy",
            "--config-file",
            str(strict),
            # Its own cache: this config differs from `make typecheck`'s.
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Found" in result.stdout, (
        f"mypy did not run as expected:\n{result.stdout}{result.stderr}"
    )
    flagged = {
        _module_name(match.group(1))
        for match in re.finditer(r"^(\S+\.py):\d+: error: .*\[explicit-any\]$", result.stdout, re.M)
    }
    stale = sorted(set(MYPY_BASELINE) - flagged)
    assert not stale, f"typed but still excused in pyproject.toml, remove them: {stale}"


def _module_name(path: str) -> str:
    module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return module.removesuffix(".__init__")

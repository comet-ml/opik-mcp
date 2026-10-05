"""`server.json` is the MCP Registry entry, and `release.yaml` publishes it.

The registry is what galleries and registry-aware clients install from, and it
takes each version once. A wrong entry sends them to the wrong server until the
next release, so what the entry says and what publishing it needs are pinned
here rather than found at release time.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict

from tests.repo.settings_env import env_names_the_server_reads

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


class _EnvironmentVariable(TypedDict):
    name: str


class _Package(TypedDict):
    registryType: str
    identifier: str
    runtimeHint: NotRequired[str]
    environmentVariables: NotRequired[list[_EnvironmentVariable]]


class _RegistryEntry(TypedDict):
    name: str
    packages: list[_Package]


class _Project(TypedDict):
    name: str
    readme: str


def _registry_entry() -> _RegistryEntry:
    entry: _RegistryEntry = json.loads((REPO_ROOT / "server.json").read_text())
    return entry


def _project() -> _Project:
    project: _Project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    return project


def _mcp_publisher_pin(workflow: str) -> tuple[str, str] | None:
    version = re.search(r'MCP_PUBLISHER_VERSION: "([^"]+)"', workflow)
    checksum = re.search(r"MCP_PUBLISHER_SHA256: (\w+)", workflow)
    if version is None or checksum is None:
        return None
    return version.group(1), checksum.group(1)


def test_the_registry_entry_installs_the_pypi_package_with_uvx() -> None:
    project_name = _project()["name"]
    packages = [
        (package["registryType"], package["identifier"], package.get("runtimeHint"))
        for package in _registry_entry()["packages"]
    ]
    assert packages == [("pypi", project_name, "uvx")], (
        f"server.json lists the packages {packages}; it must list exactly the PyPI package "
        f"{project_name!r}, run with uvx. An npm package there sends registry clients to the "
        "deprecated TypeScript server."
    )


def test_every_registry_env_var_is_one_the_server_reads() -> None:
    advertised = {
        variable["name"]
        for package in _registry_entry()["packages"]
        for variable in package.get("environmentVariables", [])
    }
    assert "OPIK_API_KEY" in advertised, (
        f"server.json advertises the env vars {sorted(advertised)}, without OPIK_API_KEY. "
        "A gallery asks only for the variables listed under `environmentVariables`, so a "
        "missing or misspelled list leaves Cloud users with no way to enter their key."
    )
    unread = advertised - env_names_the_server_reads()
    assert not unread, (
        f"server.json advertises env vars that Settings in src/opik_mcp/config.py never reads: "
        f"{sorted(unread)}. A gallery prompts the user for each one, so remove them or rename "
        "them to the variable the server reads."
    )


def test_the_pypi_description_carries_the_line_the_registry_checks() -> None:
    name = _registry_entry()["name"]
    readme = _project()["readme"]
    description = (REPO_ROOT / readme).read_text()
    # The registry needs a boundary after the name: whitespace, a tag or `-->`.
    assert re.search(rf"mcp-name: {re.escape(name)}(?=\s|<|-->|$)", description), (
        f"the PyPI description ({readme}) has no "
        f"`mcp-name: {name}` line. The MCP Registry looks for it on PyPI before it accepts "
        "server.json, so the release's registry job fails without it."
    )


def test_the_typescript_workflow_on_main_has_no_registry_step() -> None:
    workflow = (WORKFLOWS / "legacy-ts-deploy.yml").read_text()
    assert "mcp-publisher" not in workflow, (
        "main's .github/workflows/legacy-ts-deploy.yml runs mcp-publisher again. Its 2.x "
        "versions outrank every 0.x Python version, so a TypeScript release would make the "
        "registry point clients at the deprecated server. release.yaml is the only publisher. "
        "This test cannot see the copy at an npm-v tag, which a release actually runs; "
        "legacy/typescript/DEPRECATED.md says to delete the steps there."
    )


def test_the_release_publishes_to_the_registry_only_after_pypi() -> None:
    workflow = (WORKFLOWS / "release.yaml").read_text()
    job = re.search(r"^  mcp-registry:\n(?:(?:    .*)?\n)*", workflow, re.MULTILINE)
    assert job, "release.yaml has no `mcp-registry` job; that job publishes server.json."
    needs = re.search(r"^    needs: \[([^\]]*)\]", job.group(0), re.MULTILINE)
    assert needs, "the `mcp-registry` job in .github/workflows/release.yaml has no `needs:` list."
    assert "pypi" in needs.group(1).split(", "), (
        "the `mcp-registry` job in .github/workflows/release.yaml does not need `pypi`. The "
        "registry checks the PyPI description of the version being published, so it can "
        "only run once the upload is done."
    )
    assert "mcp-publisher publish" in job.group(0), (
        "the `mcp-registry` job in .github/workflows/release.yaml no longer runs "
        "`mcp-publisher publish`."
    )


def test_ci_validates_with_the_mcp_publisher_the_release_uses() -> None:
    pins = {
        name: _mcp_publisher_pin((WORKFLOWS / name).read_text())
        for name in ("ci.yaml", "release.yaml")
    }
    unpinned = sorted(name for name, pin in pins.items() if pin is None)
    assert not unpinned, (
        f"{unpinned} in .github/workflows/ install mcp-publisher without MCP_PUBLISHER_VERSION "
        "and MCP_PUBLISHER_SHA256; pin both, as the other workflow does."
    )
    assert len(set(pins.values())) == 1, (
        f"mcp-publisher pins differ between .github/workflows/ci.yaml and release.yaml: {pins}. "
        "CI validates server.json with the same build the release publishes it with; set "
        "MCP_PUBLISHER_VERSION and MCP_PUBLISHER_SHA256 to the same values in both."
    )

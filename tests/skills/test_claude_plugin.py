"""`.claude-plugin/marketplace.json` is the Claude Code plugin: the skills and the hosted server.

Two installers read the file. `claude plugin install opik@opik` loads the
plugin, and `npx skills add comet-ml/opik-mcp` reads the same entry to find the
skills. `claude plugin validate` (the `plugin-validate` make target) checks the
schema, but not an MCP server declared on a marketplace entry, and not that
both installers resolve the same directory. These tests pin both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NotRequired, TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
AUTHORED_SKILLS = REPO_ROOT / "src" / "opik_mcp" / "skills"
# Every default location Claude Code reads under a plugin root, except skills/.
PLUGIN_DEFAULT_PATHS = (
    ".claude-plugin",
    ".mcp.json",
    ".lsp.json",
    "agents",
    "bin",
    "commands",
    "hooks",
    "monitors",
    "output-styles",
    "settings.json",
    "themes",
    "workflows",
)


class _Remote(TypedDict):
    url: str


class _RegistryEntry(TypedDict):
    remotes: list[_Remote]


class _PluginEntry(TypedDict):
    name: str
    source: str
    version: NotRequired[str]
    mcpServers: NotRequired[dict[str, dict[str, str]]]


class _MarketplaceMetadata(TypedDict, total=False):
    pluginRoot: str


class _Marketplace(TypedDict):
    name: str
    metadata: NotRequired[_MarketplaceMetadata]
    plugins: list[_PluginEntry]


def _marketplace() -> _Marketplace:
    marketplace: _Marketplace = json.loads(MARKETPLACE.read_text())
    return marketplace


def _plugin() -> _PluginEntry:
    (plugin,) = (p for p in _marketplace()["plugins"] if p["name"] == "opik")
    return plugin


def _plugin_root() -> Path:
    return (REPO_ROOT / _plugin()["source"]).resolve()


def _hosted_server_url() -> str:
    registry: _RegistryEntry = json.loads((REPO_ROOT / "server.json").read_text())
    (remote,) = registry["remotes"]
    return remote["url"]


def test_both_installers_resolve_the_authored_skills_directory() -> None:
    # Claude Code ignores `metadata.pluginRoot` for a source that starts with
    # `./`; `npx skills` joins the two. With both set, the installers disagree:
    # `"pluginRoot": "./src/opik_mcp", "source": "./"` gave Claude Code 0 skills.
    assert "pluginRoot" not in _marketplace().get("metadata", {}), (
        f"{MARKETPLACE}: set the plugin root in the entry's `source`, not in "
        "`metadata.pluginRoot`, which Claude Code ignores for a `./` source"
    )
    assert _plugin()["source"].startswith("./")
    assert _plugin_root() / "skills" == AUTHORED_SKILLS.resolve(), (
        f"{MARKETPLACE}: the opik entry's `source` must be the directory that holds "
        "src/opik_mcp/skills, so the plugin loads the authored skills"
    )


def test_the_plugin_registers_the_hosted_server_with_no_key() -> None:
    assert _plugin().get("mcpServers") == {
        "opik-mcp": {"type": "http", "url": _hosted_server_url()},
    }, (
        f"{MARKETPLACE}: the plugin registers the hosted server as `opik-mcp`, the "
        "server name every install path uses, at the URL in server.json; sign-in is "
        "OAuth, so no headers"
    )


def test_the_entry_is_the_manifest_and_the_package_gains_no_plugin_file() -> None:
    # The plugin root is the Python package: a manifest there ships in the wheel
    # and the image, and any default component path loads in every plugin user's
    # session. claude.ai and Cowork also refuse a plugin with a top-level bin/.
    plugin_files = [path for path in PLUGIN_DEFAULT_PATHS if (_plugin_root() / path).exists()]
    assert plugin_files == [], (
        f"{_plugin_root()} holds {plugin_files}, which Claude Code reads as plugin "
        f"files: declare the plugin in {MARKETPLACE} and keep these out of the package"
    )


def test_users_track_commits_rather_than_a_pinned_version() -> None:
    assert "version" not in _plugin(), (
        f"{MARKETPLACE}: a `version` keeps every user on it until it changes, and "
        "version.txt names an unreleased version; leave it out so updates follow main"
    )

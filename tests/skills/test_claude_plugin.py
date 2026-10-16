"""Claude Code and `npx skills` read the plugin root in the marketplace file differently.

Claude Code ignores `metadata.pluginRoot` for a `source` that starts with `./`;
`npx skills` joins the two. `claude plugin validate` and
`make skills-verify-source` both passed on `"pluginRoot": "./src/opik_mcp",
"source": "./"`, which gave Claude Code 0 skills.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NotRequired, TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
AUTHORED_SKILLS = REPO_ROOT / "src" / "opik_mcp" / "skills"


class _PluginEntry(TypedDict):
    name: str
    source: str


class _MarketplaceMetadata(TypedDict, total=False):
    pluginRoot: str


class _Marketplace(TypedDict):
    metadata: NotRequired[_MarketplaceMetadata]
    plugins: list[_PluginEntry]


def test_both_installers_resolve_the_authored_skills_directory() -> None:
    marketplace: _Marketplace = json.loads(MARKETPLACE.read_text())
    (plugin,) = (p for p in marketplace["plugins"] if p["name"] == "opik")
    assert "pluginRoot" not in marketplace.get("metadata", {}), (
        f"{MARKETPLACE}: set the plugin root in the entry's `source`, not in "
        "`metadata.pluginRoot`, which Claude Code ignores for a `./` source"
    )
    assert plugin["source"].startswith("./")
    assert (REPO_ROOT / plugin["source"]).resolve() / "skills" == AUTHORED_SKILLS.resolve(), (
        f"{MARKETPLACE}: the opik entry's `source` must be the directory that holds "
        "src/opik_mcp/skills, so the plugin loads the authored skills"
    )

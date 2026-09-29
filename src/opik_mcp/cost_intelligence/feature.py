"""What an AI Spend workspace adds outside its entity types: one sentence on the
``read`` and ``list`` descriptions, one ``initialize`` paragraph and the guide."""

from __future__ import annotations

from importlib.resources import files
from typing import Final

from opik_mcp.config import AI_SPEND_FEATURE
from opik_mcp.features.feature import Feature

READ_SENTENCE: Final = "In this AI Spend workspace, the spend_* types are also readable."
LIST_SENTENCE: Final = "In this AI Spend workspace, the spend_* types are also listable."

INSTRUCTIONS_PARAGRAPH: Final = (
    "AI Spend workspace: Claude Code usage is logged to project `claude-code`. "
    "Dollars come from the spend_* types (list/read), ranked by tokens. "
    "Call read_skill('cost-intelligence') first."
)

GUIDE_NAME: Final = "cost-intelligence"


def _read_guide() -> str:
    """The guide lives outside the skills tree, so resources and the published
    pack never see it."""
    guide = files("opik_mcp") / "cost_intelligence" / "cost-intelligence.md"
    return guide.read_text(encoding="utf-8")


FEATURE: Final = Feature(
    name=AI_SPEND_FEATURE,
    tool_sentences={"read": READ_SENTENCE, "list": LIST_SENTENCE},
    instructions_paragraph=INSTRUCTIONS_PARAGRAPH,
    skills={GUIDE_NAME: _read_guide},
)

"""What a cost intelligence workspace adds: one sentence on the ``read`` and
``list`` descriptions, one ``initialize`` paragraph and the guide."""

from __future__ import annotations

from importlib.resources import files
from typing import Final

READ_SENTENCE: Final = "In this AI Spend workspace, spend_lane and spend_session are also readable."
LIST_SENTENCE: Final = "In this AI Spend workspace, the five spend_* types are also listable."

INSTRUCTIONS_PARAGRAPH: Final = (
    "AI Spend workspace: Claude Code usage is logged to project `claude-code`. "
    "Dollars come from the spend_* types (list/read), ranked by tokens. "
    "Call read_skill('cost-intelligence') first."
)

GUIDE_NAME: Final = "cost-intelligence"


def read_guide() -> str:
    """The guide lives outside the skills tree, so resources and the published
    pack never see it."""
    guide = files("opik_mcp") / "cost_intelligence" / "cost-intelligence.md"
    return guide.read_text(encoding="utf-8")

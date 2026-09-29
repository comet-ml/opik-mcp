"""What an AI Spend workspace adds to the surface: one sentence on the ``read``
and ``list`` descriptions and one paragraph of ``initialize`` instructions."""

from __future__ import annotations

from typing import Final

GUIDE_NAME: Final = "cost-intelligence"
GUIDE_FILE: Final = "cost-intelligence.md"

READ_SENTENCE: Final = "In this AI Spend workspace, the spend_* types are also readable."
LIST_SENTENCE: Final = "In this AI Spend workspace, the spend_* types are also listable."

INSTRUCTIONS_PARAGRAPH: Final = (
    "AI Spend workspace: Claude Code usage is logged to project `claude-code`. "
    "Dollars come from the spend_* types (list/read), ranked by tokens. "
    "Call read_skill('cost-intelligence') first."
)

__all__ = [
    "GUIDE_FILE",
    "GUIDE_NAME",
    "INSTRUCTIONS_PARAGRAPH",
    "LIST_SENTENCE",
    "READ_SENTENCE",
]

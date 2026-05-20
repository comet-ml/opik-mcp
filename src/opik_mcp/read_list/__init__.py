"""Universal read / list tool surface.

Replaces the ``opik://`` resource family (ADR 0004 D1). The agent calls
``read(entity_type, id, max_tokens=None)`` and ``list(entity_type, name=None,
page=1, size=25)`` against the same entity registry instead of
``resources/read`` against URI templates — resources are invisible to
Claude Code and only partially visible to Cursor, so reads must live on
the tools surface to be usable by the primary host.

The shape mirrors ollie-assist's read/list tools (the patterns are ported
verbatim where useful), but the entity layer uses opik-mcp's own
``OpikClient`` instead of the ``opik`` SDK to keep the runtime dep set
minimal.
"""

from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read

__all__ = ["run_list", "run_read"]

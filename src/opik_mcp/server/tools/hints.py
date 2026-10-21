from __future__ import annotations

from mcp.types import ToolAnnotations

# Hints hosts read without the schema. Claude Code runs read-only tools in
# parallel. `write` counts as destructive because trace.update and the issue
# and thread state changes rewrite records that already exist.
READS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITES = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)

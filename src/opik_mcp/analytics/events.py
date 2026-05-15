"""Event-name constants + low-cardinality bucket helpers.

Buckets are deliberate: they give actionable distributions without leaking
identifiable values. Thresholds picked to align with common LLM-context budgets
(~2k / ~8k / ~32k tokens) and to keep `tool_called` properties stringifiable.
"""

from __future__ import annotations

EVENT_SERVER_STARTED = "opik_mcp_server_started"
EVENT_SESSION_INITIALIZED = "opik_mcp_session_initialized"
EVENT_TOOL_CALLED = "opik_mcp_tool_called"
EVENT_ASK_OLLIE_COMPLETED = "opik_mcp_ask_ollie_completed"
EVENT_AUTO_APPROVAL = "opik_mcp_auto_approval"


def bucket_tokens(n: int) -> str:
    if n < 2_000:
        return "<2k"
    if n < 8_000:
        return "2k-8k"
    if n < 32_000:
        return "8k-32k"
    return ">32k"


def bucket_text_len(s: str | None) -> str:
    n = len(s) if s else 0
    if n < 100:
        return "<100"
    if n < 1000:
        return "100-1000"
    return ">1000"


def bucket_count(n: int) -> str:
    if n == 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 100:
        return "11-100"
    if n <= 1_000:
        return "101-1000"
    return ">1000"

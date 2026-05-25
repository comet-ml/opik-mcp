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
# Emitted from the startup path when the server fails to come up — settings
# validation crash, refused HTTP bind, or transport.run() exception. Pairs
# with ``opik_mcp_server_started`` to form an install-funnel: started without
# a matching error = healthy boot; either alone signals a problem.
EVENT_STARTUP_ERROR = "opik_mcp_startup_error"
EVENT_TOOLS_LISTED = "opik_mcp_tools_listed"
# Pairs with server_started. Carries handshake-progress flags
# (first_rpc_received, session_reached) and lifespan bucket so BI can
# slice the dark cohort into {pure probe, handshake-failed, healthy-short,
# healthy-long}.
EVENT_SERVER_SHUTDOWN = "opik_mcp_server_shutdown"


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


def bucket_seconds(n: float) -> str:
    # <5s isolates probe / crash-loop traffic from "real client connected
    # and disconnected before completing the handshake" (5-60s).
    if n < 5:
        return "<5s"
    if n < 60:
        return "5-60s"
    if n < 600:
        return "1-10m"
    if n < 3600:
        return "10-60m"
    if n < 86400:
        return "1-24h"
    return ">24h"

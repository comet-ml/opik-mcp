"""Process-global handshake-progress flags for server_shutdown event.

Two booleans that flip during the lifetime of an opik-mcp process:

- ``first_rpc_received``: any MCP message reached the server (the transport
  delivered at least one RPC). Discriminates pure probes (server started,
  client never wrote anything) from real-but-stalled clients.
- ``session_reached``: ``_maybe_emit_session_initialized`` fired, meaning a
  tool call ran past initialization. Pairs with ``first_rpc_received`` to
  slice the dark cohort.

Both are module-level globals because they're read once at process shutdown
(from the FastMCP ``lifespan=`` context manager in ``__main__``) and written
from the wrappers / list_tools handler. A class would force every emit site
to thread an instance through; the global keeps the call sites trivial.

NEVER call ``reset_for_tests`` from production code.
"""

from __future__ import annotations

_first_rpc_received: bool = False
_session_reached: bool = False


def mark_first_rpc() -> None:
    global _first_rpc_received
    _first_rpc_received = True


def mark_session_reached() -> None:
    global _session_reached
    _session_reached = True


def first_rpc_received() -> bool:
    return _first_rpc_received


def session_reached() -> bool:
    return _session_reached


def reset_for_tests() -> None:
    """Drop both flags. Test-only — never call from production code."""
    global _first_rpc_received, _session_reached
    _first_rpc_received = False
    _session_reached = False

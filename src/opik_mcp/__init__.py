"""opik-mcp package.

Intentionally empty at import time. We previously re-exported
``opik_mcp.server.mcp`` here for convenience, but ``server`` constructs the
``FastMCP`` instance with ``instructions=render_instructions()`` — which
eagerly calls ``get_settings()``. That made a ``ValidationError`` from
malformed env (e.g. bad ``COMET_WORKSPACE_ID``) propagate out of
``import opik_mcp`` itself, before ``__main__.main()`` could catch it and
emit ``opik_mcp_startup_error``. The fallback-client emit path was dead
code on the most common install-failure mode.

Anything that needs the server should import it directly:
``from opik_mcp.server import mcp``.
"""

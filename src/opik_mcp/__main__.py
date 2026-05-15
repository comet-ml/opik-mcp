import logging
import sys

import uvicorn

from opik_mcp.analytics import EVENT_SERVER_STARTED, track_event
from opik_mcp.config import get_settings

logger = logging.getLogger("opik_mcp")

INSECURE_DEFAULT_TOKEN = "dev-token-123"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main() -> None:
    settings = get_settings()
    _configure_logging(settings.opik_mcp_log_level)
    transport = settings.opik_mcp_transport.lower()

    track_event(
        EVENT_SERVER_STARTED,
        {
            "transport": transport,
            "analytics_enabled": str(settings.opik_mcp_analytics_enabled).lower(),
            "has_workspace": str(settings.comet_workspace is not None).lower(),
            "has_api_key": str(settings.opik_api_key is not None).lower(),
            "has_default_project": str(settings.opik_default_project_name is not None).lower(),
        },
    )

    if transport == "stdio":
        # Default: Claude Code (or any MCP client) launches this process and
        # speaks MCP over stdin/stdout. No port, no bearer token, no uvicorn.
        # OPIK_MCP_DEV_TOKEN is only relevant in HTTP mode (see below).
        from opik_mcp.server import mcp

        logger.info("startup transport=stdio")
        mcp.run(transport="stdio")
        return

    logger.info(
        "startup transport=http host=%s port=%s reload=%s",
        settings.opik_mcp_host,
        settings.opik_mcp_port,
        settings.opik_mcp_reload,
    )

    if settings.opik_mcp_dev_token == INSECURE_DEFAULT_TOKEN:
        logger.warning(
            "OPIK_MCP_DEV_TOKEN is using the insecure default %r. "
            "Set a strong secret before exposing this server beyond localhost.",
            INSECURE_DEFAULT_TOKEN,
        )

    # Imported lazily so stdio mode doesn't pay the Starlette import cost.
    from opik_mcp.server import build_app

    if settings.opik_mcp_reload:
        uvicorn.run(
            "opik_mcp.server:build_app",
            factory=True,
            host=settings.opik_mcp_host,
            port=settings.opik_mcp_port,
            reload=True,
            reload_dirs=["src"],
        )
    else:
        uvicorn.run(build_app(), host=settings.opik_mcp_host, port=settings.opik_mcp_port)


if __name__ == "__main__":
    main()

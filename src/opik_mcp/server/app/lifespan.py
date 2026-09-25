from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from opik_mcp.analytics import (
    EVENT_SERVER_SHUTDOWN,
    EVENT_SERVER_STARTED,
    boot_props,
    get_analytics,
    track_event,
)
from opik_mcp.config import Settings

logger = logging.getLogger("opik_mcp")


# Shutdown drain budget for the lifespan path. Mirrors __main__'s deadline: the
# daemon worker is about to be torn down, so block briefly to land the POST.
_LIFESPAN_FLUSH_DEADLINE_S = 3.5


def _make_composed_lifespan(
    inner_lifespan: Any,
    settings: Settings,
    fingerprint_props: dict[str, str],
) -> Any:
    """Wrap FastMCP's session-manager lifespan with analytics lifecycle emits.

    Closes GAP#1: the hosted entrypoint runs ``uvicorn ... build_app --factory``,
    which calls ``build_app()`` directly and never runs ``__main__.main()``, so
    the boot funnel was 100% dark. This emits server_started/shutdown from the
    lifespan instead — UNLESS ``main()`` owns lifecycle (the sentinel), in which
    case it just runs the inner lifespan and emits nothing (no double-count).

    ``inner_lifespan`` MUST be captured from ``app.router.lifespan_context``
    BEFORE it is overwritten — it starts the ``StreamableHTTPSessionManager``,
    without which every MCP request hangs.
    """

    @contextlib.asynccontextmanager
    async def _composed(app: Any) -> AsyncIterator[Any]:
        if boot_props.lifecycle_owned_by_main():
            # main() emits the lifecycle events; just run the session manager.
            async with inner_lifespan(app) as state:
                yield state
            return

        # Anchor at lifespan enter (when serving actually starts), not at
        # build_app() time — keeps lifespan_seconds_bucket free of uvicorn's
        # startup/bind latency.
        started_monotonic = time.monotonic()
        try:
            track_event(
                EVENT_SERVER_STARTED,
                boot_props.server_started_props(
                    settings, fingerprint_props=fingerprint_props, lifecycle_source="lifespan"
                ),
            )
        except Exception:
            logger.debug("lifespan server_started emit failed", exc_info=True)

        reason = "clean_exit"
        try:
            async with inner_lifespan(app) as state:
                yield state
        except BaseException:
            reason = "transport_error"
            raise
        finally:
            try:
                elapsed = time.monotonic() - started_monotonic
                track_event(
                    EVENT_SERVER_SHUTDOWN,
                    boot_props.server_shutdown_props(
                        reason=reason, elapsed_seconds=elapsed, lifecycle_source="lifespan"
                    ),
                )
                # flush() blocks on threading.Event.wait — never block the event
                # loop; offload the drain to the default executor. Capture the
                # client now (not inside the thread) so a concurrent singleton
                # swap can't redirect the flush to a different/closed client.
                client = get_analytics()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, lambda: client.flush(deadline_s=_LIFESPAN_FLUSH_DEADLINE_S)
                )
            except BaseException:
                # Mirror __main__._emit_server_shutdown: a telemetry-side failure
                # (incl. CancelledError from the executor during loop teardown)
                # must NEVER mask the real shutdown reason or leak out of finally.
                logger.debug("lifespan server_shutdown emit failed", exc_info=True)

    return _composed

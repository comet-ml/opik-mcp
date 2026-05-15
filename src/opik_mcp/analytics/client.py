"""Fire-and-forget HTTP transport for opik-mcp analytics.

Daemon-thread worker model (not asyncio): callable from any context, including
`__main__.main()` before the MCP runtime has started a loop.
"""

from __future__ import annotations

import logging
import platform
import queue
import sys
import threading
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from opik_mcp.analytics.identity import get_install_id, resolve_anonymous_id
from opik_mcp.config import Settings

logger = logging.getLogger("opik_mcp.analytics")

_QUEUE_SENTINEL: Any = object()


def _resolve_version() -> str:
    try:
        return version("opik-mcp")
    except PackageNotFoundError:
        return "unknown"


# Cached once at import time — package metadata is static for the process lifetime.
_OPIK_MCP_VERSION: str = _resolve_version()


class AnalyticsClient:
    """Thread-safe, fire-and-forget event sender."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        max_queue_size: int = 100,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(
                connect=settings.opik_mcp_analytics_connect_timeout_s,
                read=settings.opik_mcp_analytics_total_timeout_s,
                write=settings.opik_mcp_analytics_total_timeout_s,
                pool=settings.opik_mcp_analytics_total_timeout_s,
            )
        )
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)
        self._worker: threading.Thread | None = None
        self._closed = False
        self._closed_lock = threading.Lock()
        # Signaled by the worker when the queue drains to empty.
        self._idle_event = threading.Event()
        self._idle_event.set()  # no events pending at construction
        if self._settings.opik_mcp_analytics_enabled:
            self._start_worker()

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        if not self._settings.opik_mcp_analytics_enabled:
            return
        with self._closed_lock:
            if self._closed:
                return
        try:
            event = self._build_event(event_type, properties)
            try:
                self._queue.put_nowait(event)
                self._idle_event.clear()
            except queue.Full:
                logger.debug("analytics queue full; dropping event_type=%s", event_type)
        except Exception:
            # Last-resort guard — track_event MUST NEVER raise.
            logger.warning("analytics.track_event swallowed exception", exc_info=True)

    def _flush(self, *, deadline_s: float = 2.0) -> None:
        """Block until all enqueued tasks are fully processed or deadline elapses.

        Test-only convenience.  Uses queue.join() semantics (waits until every
        task_done() has been called) rather than checking queue.empty(), which is
        non-atomic: a producer mid-enqueue can make the queue look empty while an
        unfinished task is still in flight.

        queue.Queue.join() blocks indefinitely; we run it in a daemon thread so
        we can enforce the deadline from the calling thread.
        """
        done = threading.Event()

        def _join() -> None:
            self._queue.join()
            done.set()

        t = threading.Thread(target=_join, daemon=True)
        t.start()
        done.wait(timeout=deadline_s if deadline_s > 0 else None)

    def close(self) -> None:
        with self._closed_lock:
            self._closed = True
        if self._worker is None:
            self._http.close()
            return
        self._queue.put(_QUEUE_SENTINEL)
        self._worker.join(timeout=2.0)
        self._http.close()

    # ------ internals ------

    def _start_worker(self) -> None:
        if self._worker is not None:
            return
        t = threading.Thread(
            target=self._run_worker,
            name="opik-mcp-analytics",
            daemon=True,
        )
        self._worker = t
        t.start()

    def _run_worker(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is _QUEUE_SENTINEL:
                    return
                try:
                    resp = self._http.post(self._settings.opik_mcp_analytics_url, json=event)
                    resp.raise_for_status()
                except Exception:
                    logger.warning("analytics POST failed", exc_info=True)
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle_event.set()

    def _build_event(self, event_type: str, properties: dict[str, str]) -> dict[str, Any]:
        common: dict[str, str] = {
            "environment": self._settings.opik_mcp_analytics_environment,
            "opik_mcp_version": _OPIK_MCP_VERSION,
            "transport": self._settings.opik_mcp_transport,
            "install_id": get_install_id(),
            "python_version": (
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            "platform": platform.system(),
        }
        if self._settings.comet_workspace:
            common["workspace_id"] = self._settings.comet_workspace
        # Common props (environment, version, transport, …) are authoritative: a
        # call site accidentally passing e.g. "environment" must not silently
        # shadow the server-stamped value.  Spread caller properties first so
        # common always wins on key conflicts.
        return {
            "anonymous_id": resolve_anonymous_id(self._settings),
            "event_type": event_type,
            "event_properties": {**properties, **common},
        }

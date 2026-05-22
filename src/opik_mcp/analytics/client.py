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
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from opik_mcp.analytics.identity import api_key_sha256, get_install_id, resolve_anonymous_id
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
        if self._settings.opik_mcp_analytics_enabled:
            self._warn_if_misconfigured_for_onprem()
            self._start_worker()

    def _warn_if_misconfigured_for_onprem(self) -> None:
        """One-shot WARNING when `source` looks cloud but URLs look on-prem.

        Safety net for on-prem deploys that forget to set
        ``OPIK_MCP_ANALYTICS_SOURCE=""`` — without it, events would phone home
        to ``stats.comet.com`` self-labelled as a cloud-Comet client.
        """
        source = self._settings.opik_mcp_analytics_source
        comet_url = self._settings.comet_url_override or ""
        if source == "comet.com" and "comet.com" not in comet_url.lower():
            logger.warning(
                "analytics source=%r but COMET_URL_OVERRIDE=%r looks on-prem; "
                "set OPIK_MCP_ANALYTICS_SOURCE='' (or your domain) to avoid "
                "mis-labelling events as cloud-Comet.",
                source,
                comet_url,
            )

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
            except queue.Full:
                logger.debug("analytics queue full; dropping event_type=%s", event_type)
        except Exception:
            # Last-resort guard — track_event MUST NEVER raise.
            logger.warning("analytics.track_event swallowed exception", exc_info=True)

    def flush(self, *, deadline_s: float = 2.0) -> None:
        """Block until all enqueued tasks are fully processed or deadline elapses.

        Production call site: ``__main__`` on the startup-error path, where
        the process is about to ``sys.exit`` / re-raise and the daemon worker
        would otherwise be killed mid-POST. Also used by tests to deterministic-
        ally drain the queue.

        Uses ``queue.join()`` semantics (waits until every ``task_done()`` has
        been called) rather than checking ``queue.empty()``, which is non-atomic:
        a producer mid-enqueue can make the queue look empty while an
        unfinished task is still in flight.

        ``queue.Queue.join()`` blocks indefinitely; we run it in a daemon
        thread so we can enforce the deadline from the calling thread.
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
            # Client-stamped timestamp — matches ollie-assist `bi.track()` so
            # events from both products correlate cleanly in BI. The receiver
            # also stamps an arrival time, but the client timestamp is what
            # actually maps to when the user took the action.
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if self._settings.comet_workspace:
            common["workspace"] = self._settings.comet_workspace
        if self._settings.comet_workspace_id:
            # Stable UUID for the workspace — preferred join key in BI; the
            # workspace `name` is human-readable but mutable.
            common["workspace_id"] = self._settings.comet_workspace_id
        if self._settings.opik_api_key:
            # Pseudonymous per-user identity. The raw key NEVER leaves the
            # process; the backend retains the raw-key → user-id mapping and
            # can JOIN on this digest to recover the Comet user account.
            common["api_key_sha256"] = api_key_sha256(self._settings.opik_api_key)
        if self._settings.opik_mcp_analytics_source:
            # Tells comet-stats to mark `on_prem=False` and skip IP enrichment;
            # matches the `OLLIE_SOURCE` / opik.sh convention.
            common["source"] = self._settings.opik_mcp_analytics_source
        # Common props (environment, version, transport, …) are authoritative: a
        # call site accidentally passing e.g. "environment" must not silently
        # shadow the server-stamped value.  Spread caller properties first so
        # common always wins on key conflicts.
        return {
            # comet-stats indexes events by top-level `user_id`. Kept as
            # workspace name → install_id for dashboard continuity. The
            # per-user identity is in event_properties.api_key_sha256.
            "user_id": resolve_anonymous_id(self._settings),
            "event_type": event_type,
            "event_properties": {**properties, **common},
        }

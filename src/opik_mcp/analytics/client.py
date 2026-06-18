"""Fire-and-forget HTTP transport for opik-mcp analytics.

Daemon-thread worker model (not asyncio): callable from any context, including
`__main__.main()` before the MCP runtime has started a loop.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import queue
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from opik_mcp.analytics.identity import (
    OPIK_MCP_VERSION,
    api_key_sha256,
    get_install_id,
    resolve_anonymous_id,
)
from opik_mcp.auth_context import (
    classify_bearer,
    inbound_authorization,
    inbound_workspace,
    settings_auth_mode,
)
from opik_mcp.config import Settings, installation_type

logger = logging.getLogger("opik_mcp.analytics")

_QUEUE_SENTINEL: Any = object()


class AnalyticsClient:
    """Thread-safe, fire-and-forget event sender."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        max_queue_size: int = 100,
        retry_backoff_s: tuple[float, ...] = (0.0, 0.5, 1.5),
    ) -> None:
        self._settings = settings
        # One entry per delivery attempt; the value is the delay (seconds) to
        # sleep *before* that attempt. The first 0.0 means "try immediately".
        # The second attempt reuses the pooled connection the first one warmed,
        # which is why a single retry recovers the lost-cold-POST case. Empty is
        # normalised to one immediate attempt so a stray ``()`` can never mean
        # "never send" (it would silently drop every event with zero attempts).
        self._retry_backoff_s = retry_backoff_s or (0.0,)
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
        # Process-stable destination class — computed ONCE (settings are fixed
        # for this client) so _build_event doesn't re-parse the URL per event.
        # Falls back to "unknown" rather than dropping the key on any failure.
        try:
            self._installation_type = installation_type(settings)
        except Exception:
            logger.debug("installation_type computation failed", exc_info=True)
            self._installation_type = "unknown"
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
                self._dispatch_with_retry(event)
            finally:
                self._queue.task_done()

    def _dispatch_with_retry(self, event: dict[str, Any]) -> None:
        """POST one event, retrying transient failures per ``_retry_backoff_s``.

        The first cold POST routinely loses the DNS+TLS race; a retry on the
        warmed pooled connection lands. We retry on *any* exception (network,
        timeout, 5xx via ``raise_for_status``) because at this layer they are
        indistinguishable from the transient class we care about, and a wasted
        retry on a genuine permanent error is cheap (events are tiny).
        """
        for delay in self._retry_backoff_s:
            if delay:
                time.sleep(delay)
            try:
                resp = self._http.post(self._settings.opik_mcp_analytics_url, json=event)
                resp.raise_for_status()
                return
            except Exception:
                logger.debug("analytics POST attempt failed", exc_info=True)
        logger.warning(
            "analytics POST failed after %d attempt(s) for event_type=%s",
            len(self._retry_backoff_s),
            event.get("event_type"),
        )

    def _build_event(self, event_type: str, properties: dict[str, str]) -> dict[str, Any]:
        common: dict[str, str] = {
            "environment": self._settings.opik_mcp_analytics_environment,
            "opik_mcp_version": OPIK_MCP_VERSION,
            # Lowercased so BI sees a canonical value regardless of how the env
            # var was cased (main() also lowercases for transport selection).
            "transport": self._settings.opik_mcp_transport.lower(),
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
        # Process-stable Opik destination class (computed once in __init__),
        # stamped on every event so BI can split cloud / self-hosted without
        # joining back to server_started.
        common["installation_type"] = self._installation_type

        per_request = self._per_request_props()
        # Merge precedence (lowest → highest): per-request contextvar enrichment,
        # then caller-supplied properties, then the authoritative common block.
        # common always wins (a call site accidentally passing e.g. "environment"
        # must not shadow the server-stamped value); caller properties win over
        # per-request so server_started's settings-derived auth_mode beats the
        # contextvar-derived one (which is "none" at boot, no request in flight).
        return {
            # comet-stats indexes events by top-level `user_id`. Kept as
            # workspace name → install_id for dashboard continuity. The
            # per-user identity is in event_properties.api_key_sha256.
            "user_id": resolve_anonymous_id(self._settings),
            "event_type": event_type,
            "event_properties": {**per_request, **properties, **common},
        }

    def _per_request_props(self) -> dict[str, str]:
        """Identity derived from the inbound-auth ContextVars (HTTP/OAuth mode).

        Runs in the caller's task (``_build_event`` builds synchronously before
        enqueuing), so the request's ContextVars are live here — this is what
        lets per-request OAuth identity reach BI in hosted mode.

        ``auth_mode`` is ALWAYS set so stdio / no-auth events are not a dark
        cohort. PRIVACY: the raw bearer token never enters the result — only its
        sha256 digest, and only for ``opik_mcp_at_`` OAuth tokens. ``request_workspace``
        mirrors the existing plaintext ``workspace`` posture (workspace names are
        used as ``user_id`` in ``resolve_anonymous_id``).
        """
        props: dict[str, str] = {}
        try:
            inbound_auth = inbound_authorization.get()
            ws_header = inbound_workspace.get()

            if inbound_auth:
                # Shared classifier (see auth_context.classify_bearer) so BI's
                # auth_mode/token_sha256 agree with the outbound credential and
                # with AuthRejectionMiddleware.
                mode, token = classify_bearer(inbound_auth)
                props["auth_mode"] = mode
                if token:  # oauth bearer
                    # PRIVACY: only the digest is emitted; the raw token never
                    # enters the result.
                    props["token_sha256"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
            else:
                # No inbound header: stdio or unauthenticated HTTP. Fall back to
                # the settings-derived mode (shared with auth_mode_at_boot, so an
                # OAuth-only deploy reports "oauth" not "none"). ALWAYS set so the
                # stdio / no-auth cohort is visible, not dark.
                props["auth_mode"] = settings_auth_mode(
                    has_api_key=bool(self._settings.opik_api_key),
                    has_as_url=bool(self._settings.opik_mcp_as_url),
                )

            workspace = ws_header.strip() if ws_header else ""
            if workspace:
                props["request_workspace"] = workspace
        except Exception:
            logger.debug("per-request identity enrichment failed", exc_info=True)
        return props

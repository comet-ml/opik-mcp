"""Sentry-based error reporting for opik-mcp.

Pairs with the analytics funnel (``opik_mcp.analytics``) but serves a different
purpose: analytics is for low-cardinality install / usage funnels, Sentry is
for stack traces of the *unexpected* failures that need a human. The two have
independent opt-out flags so a user (or on-prem install) can disable either
without affecting the other.

Filtering policy is deliberately thin compared to the opik SDK's filter chain:
we own every capture site in this codebase (the tool wrapper and the startup
error path), so "drop user-side noise" lives at the call site, not here. The
remaining ``before_send`` rules are the ones that only make sense as a global
backstop:

  - Per-process cap of 30 error events stops a retry loop or repeating bug
    from flooding the project.
  - Anything emitted from inside a pytest run is dropped (defence in depth;
    ``setup_sentry`` already refuses to init under pytest).

DSN: a public ingest key for the opik-mcp Sentry project is hardcoded as
``Settings.opik_mcp_sentry_dsn`` (a ``ClassVar``, NOT env-overridable —
see the field comment for why). The only supported opt-out is
``OPIK_MCP_SENTRY_ENABLED=false``; without it, ``setup_sentry`` is a
no-op and ``capture_exception`` becomes a no-op too (sentry-sdk swallows
captures when no client is bound).
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
from collections.abc import Callable
from typing import Any

import sentry_sdk
from sentry_sdk.types import Event, Hint

# ``installation_type`` lives in ``config`` (a leaf module) so both this Sentry
# path and ``analytics.boot_props`` (BI) share one source without an import cycle.
from opik_mcp.analytics.identity import OPIK_MCP_VERSION, get_install_id
from opik_mcp.config import Settings, installation_type

logger = logging.getLogger("opik_mcp.error_tracking")

# Per-process event cap. The number is intentionally low: a tool failing in
# a tight loop should land a handful of events, not 10k. Once the cap is
# hit the rest are dropped client-side at zero cost. Applies regardless of
# Sentry level — fatal floods are worse to swallow than error floods, and
# we don't emit non-error events in this codebase anyway.
_MAX_EVENTS: int = 30


def _in_pytest() -> bool:
    # Two signals because they cover different lifecycles: ``pytest`` in
    # ``sys.modules`` catches conftest-driven imports; ``PYTEST_CURRENT_TEST``
    # catches subprocesses pytest spawns that re-enter our code.
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


class _EventCap:
    """Caps events for the process lifetime, regardless of Sentry level.

    Thread-safe so the tool-call wrapper (anyio task threads) and the analytics
    daemon thread share one counter without racing each other past the cap.
    """

    def __init__(self, max_count: int) -> None:
        self._max = max_count
        self._count = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._count >= self._max:
                return False
            self._count += 1
            return True


def _build_before_send() -> Callable[[Event, Hint], Event | None]:
    cap = _EventCap(_MAX_EVENTS)

    def before_send(event: Event, _hint: Hint) -> Event | None:
        # Defence in depth — if init somehow ran inside pytest (e.g. a test
        # patched the pytest detector), drop everything anyway.
        if _in_pytest():
            return None
        if not cap.allow():
            return None
        return event

    return before_send


def setup_sentry(settings: Settings) -> bool:
    """Initialize Sentry and bind low-cardinality scope tags. Returns True
    iff a client was bound.

    Single-phase: opik-mcp doesn't engineer for ``Settings`` construction
    failing in practice (the values that drive validation are hardcoded
    in the deploy pipeline, not user-supplied), so there's no scenario
    where Sentry needs to be live BEFORE Settings. Keep it simple.

    Not idempotent — calling twice would reset the ``_EventCap`` counter.
    The only production caller is ``__main__``.
    """
    if not settings.opik_mcp_sentry_enabled:
        return False
    if _in_pytest():
        return False

    sentry_sdk.init(
        dsn=settings.opik_mcp_sentry_dsn,
        release=OPIK_MCP_VERSION,
        environment=settings.opik_mcp_analytics_environment,
        # Mirrors opik SDK: disable Sentry's built-in atexit/excepthook/
        # logging integrations so we own the capture sites explicitly and
        # don't accidentally report third-party tracebacks.
        default_integrations=False,
        integrations=[],
        traces_sample_rate=0.0,
        send_default_pii=False,
        before_send=_build_before_send(),
        shutdown_timeout=2.0,
    )

    _bind_scope(settings)
    logger.debug("opik-mcp Sentry error tracking enabled")
    return True


def _bind_scope(settings: Settings) -> None:
    """Stamp every event with the same low-cardinality tags + user identity.

    Identity precedence — UUID first because it's a stable join key in BI,
    workspace name second because it's human-readable, install_id last as
    an anonymous fallback. The install_id is the same one analytics uses,
    so a Sentry event correlates back to a BI row.
    """
    user_id = settings.comet_workspace_id or settings.comet_workspace or get_install_id()
    sentry_sdk.set_user({"id": user_id})

    sentry_sdk.set_tag("release", OPIK_MCP_VERSION)
    sentry_sdk.set_tag("os_type", platform.system())
    sentry_sdk.set_tag(
        "python_version",
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    sentry_sdk.set_tag("transport", settings.opik_mcp_transport)
    # Mirrors opik SDK's classification so Sentry filters stay portable
    # between products. See ``config.installation_type`` for the URL precedence.
    sentry_sdk.set_tag("installation_type", installation_type(settings))
    sentry_sdk.set_tag("github_actions", str(bool(os.getenv("GITHUB_ACTIONS"))).lower())
    if settings.opik_mcp_analytics_source:
        sentry_sdk.set_tag("source", settings.opik_mcp_analytics_source)
    if settings.comet_workspace:
        # Separate from ``user.id`` (which prefers UUID for stable joins).
        # The name is the readable filter dashboards key off of, and it's
        # the only identifier on the no-UUID path.
        sentry_sdk.set_tag("workspace", settings.comet_workspace)
    sentry_sdk.set_tag("has_workspace_id", str(bool(settings.comet_workspace_id)).lower())
    sentry_sdk.set_tag("has_api_key", str(bool(settings.opik_api_key)).lower())


def capture_exception(
    exc: BaseException,
    *,
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
    transaction: str | None = None,
    fingerprint: list[str] | None = None,
) -> None:
    """Forward an exception to Sentry. No-op when no client is bound.

    ``tags`` and ``extras`` are pushed onto a fresh scope cloned from the
    current one, so they apply to THIS event only — concurrent tool calls
    on the same server (HTTP transport) never see each other's context.

    ``transaction`` becomes the secondary line in Sentry's issue listing
    (e.g. ``read`` shown next to ``OpikServerError``). It's the cleanest
    way to make "which tool failed" visible without drilling into a single
    event's tags. ``fingerprint`` controls grouping — passing
    ``["{{ default }}", tool_name]`` keeps Sentry's stacktrace-based
    grouping but adds tool_name as a second key, so a shared helper
    raising the same exception type from two different tools splits into
    two issues instead of merging.

    Wrapped in try/except because capture sites are already on an error
    path — the *original* failure must propagate, never a Sentry-side bug.
    """
    try:
        with sentry_sdk.new_scope() as scope:
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)
            if extras:
                for key, value in extras.items():
                    scope.set_extra(key, value)
            if transaction:
                scope.set_transaction_name(transaction)
            if fingerprint:
                scope.fingerprint = fingerprint
            sentry_sdk.capture_exception(exc)
    except Exception:
        logger.debug("sentry capture_exception failed", exc_info=True)

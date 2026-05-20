"""MCP elicitation wiring (OPIK-6567).

Single entry point ``confirm_with_user`` that wraps the MCP elicitation
primitive with:

* capability detection from the host's ``initialize`` handshake
  (cached implicitly — the SDK reads the same client params on every
  ``check_client_capability`` call, no per-session memoization needed);
* a configurable timeout that resolves to a ``deny`` decision (safer
  default than allowing — the user can always re-issue);
* a structured ``event=elicitation`` log line on every request + decision,
  carrying tool / entity / decision / latency for auditability.

The helper returns a tri-state :class:`ElicitDecision` so the caller
decides the fallback policy (writes proceed with a warning; ``ask_ollie``
in ``disabled`` mode keeps its hard-error path). Centralizing the
capability + timeout + logging plumbing here keeps both call sites
(`writes.write_tool` and `ask_ollie`) at a single line of business logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from mcp.types import ClientCapabilities, ElicitationCapability
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context
    from mcp.server.session import ServerSession

    _Ctx = Context[ServerSession, None]
else:
    _Ctx = "Context"


logger = logging.getLogger("opik_mcp.elicitation")


class ElicitDecision(StrEnum):
    """Outcome of an elicitation round-trip.

    ``ACCEPT`` / ``DENY`` map 1:1 to the MCP spec ``accept`` / ``decline``
    actions. ``CANCEL`` covers the spec's ``cancel`` action plus our
    timeout fallback (treated as a deny by every caller, but kept distinct
    so the audit log can tell them apart).

    ``UNSUPPORTED`` is returned when the host did not advertise the
    ``elicitation`` capability on initialize — the caller picks the
    fallback (writes: proceed with warning; ask_ollie disabled: hard
    error).
    """

    ACCEPT = "accept"
    DENY = "deny"
    CANCEL = "cancel"
    UNSUPPORTED = "unsupported"

    @property
    def approved(self) -> bool:
        return self is ElicitDecision.ACCEPT


@dataclass(frozen=True)
class ElicitOutcome:
    decision: ElicitDecision
    latency_ms: int


class _YesNoForm(BaseModel):
    """Pydantic schema sent to the host for a yes/no confirmation.

    Restricted to a single primitive field because the MCP spec only
    allows primitive types in elicitation schemas — see
    ``Context.elicit`` docstring in the upstream SDK.
    """

    confirm: bool = Field(
        ...,
        description="Confirm this action? Set true to proceed, false to cancel.",
    )


_ELICIT_CAPABILITY: Final = ClientCapabilities(elicitation=ElicitationCapability())


def host_supports_elicitation(ctx: _Ctx) -> bool:
    """Probe the host's advertised ``elicitation`` capability.

    Returns ``False`` when the session is missing (defensive — should not
    happen in the tool path) or the host did not advertise the capability.
    The SDK reads from the cached ``_client_params`` set at initialize, so
    this is a cheap dict lookup; no need to memoize on our side.
    """
    try:
        session = ctx.request_context.session
    except (AttributeError, ValueError):
        # ``request_context`` is a contextvar — accessing it outside a
        # tool-call frame raises. Treat as "no host" and fall back.
        return False
    try:
        return bool(session.check_client_capability(_ELICIT_CAPABILITY))
    except Exception:  # pragma: no cover — defensive
        logger.warning("elicitation capability probe failed", exc_info=True)
        return False


async def confirm_with_user(
    ctx: _Ctx,
    *,
    prompt: str,
    timeout_s: float,
    tool: str,
    entity_type: str,
    entity_id: str | None,
) -> ElicitOutcome:
    """Ask the user to approve an action via the MCP elicitation primitive.

    Parameters mirror what we want in the audit log; the tool/entity tags
    let an operator grep ``event=elicitation`` for every confirmation a
    specific tool surfaced.

    Timeout is implemented with ``asyncio.wait_for`` rather than relying
    on the host — most hosts do not enforce one and a stuck dialog would
    otherwise pin the tool-call slot open for the host's default ceiling
    (often minutes). On timeout we emit a ``CANCEL`` outcome so callers
    can distinguish "user explicitly said no" from "user never answered".
    """
    if not host_supports_elicitation(ctx):
        logger.info(
            "event=elicitation tool=%s entity_type=%s id=%s decision=unsupported latency_ms=0",
            tool,
            entity_type,
            entity_id,
        )
        return ElicitOutcome(ElicitDecision.UNSUPPORTED, latency_ms=0)

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            ctx.elicit(message=prompt, schema=_YesNoForm),
            timeout=timeout_s if timeout_s > 0 else None,
        )
    except TimeoutError:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "event=elicitation tool=%s entity_type=%s id=%s decision=cancel "
            "reason=timeout latency_ms=%d",
            tool,
            entity_type,
            entity_id,
            latency_ms,
        )
        return ElicitOutcome(ElicitDecision.CANCEL, latency_ms=latency_ms)

    latency_ms = int((time.monotonic() - started) * 1000)
    action = getattr(result, "action", None)

    # The MCP spec uses ``accept`` / ``decline`` / ``cancel``. ``accept``
    # additionally requires the form data to be present and to set
    # ``confirm=true`` — a host that sends ``accept`` with ``confirm=false``
    # is treated as a deny (the user filled the form but explicitly
    # answered "no").
    if action == "accept":
        data = getattr(result, "data", None)
        confirm = bool(getattr(data, "confirm", False)) if data is not None else False
        decision = ElicitDecision.ACCEPT if confirm else ElicitDecision.DENY
    elif action == "decline":
        decision = ElicitDecision.DENY
    else:  # cancel, or any unexpected value — treat as cancel for safety
        decision = ElicitDecision.CANCEL

    logger.info(
        "event=elicitation tool=%s entity_type=%s id=%s decision=%s latency_ms=%d",
        tool,
        entity_type,
        entity_id,
        decision.value,
        latency_ms,
    )
    return ElicitOutcome(decision, latency_ms=latency_ms)


__all__ = [
    "ElicitDecision",
    "ElicitOutcome",
    "confirm_with_user",
    "host_supports_elicitation",
]

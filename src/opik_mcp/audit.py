"""Audit log for opik-mcp actions taken without per-action user approval.

Phase 1 backend is a dedicated `opik_mcp.audit` Python logger; rows are
emitted as `INFO` records carrying a JSON body. Phase 2 (hosted) will
extend `write_auto_approval` to also POST to the comet-backend audit
ingest endpoint — callers do not change.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from opik_mcp.analytics.events import EVENT_AUTO_APPROVAL
from opik_mcp.analytics.mcp_client_info import call_context_props
from opik_mcp.writes.registry import WRITE_OPERATIONS

# Allowlist of legitimate ``target_tool`` values for the analytics emit.
# ``target_tool`` arrives in the pod's ``confirm_required`` SSE frame as a
# free-text ``tool_name`` and is pod-controlled — never let it reach BI raw,
# or a future pod release shipping arbitrary strings would blow up our
# event cardinality. Mirrors ``ask_ollie._KNOWN_TARGET_TOOLS`` so the two
# emit sites bucket the same set. The audit ROW (logged + Phase 2 hosted
# ingest) keeps the raw value — only the analytics emit is bucketed.
_KNOWN_TARGET_TOOLS: frozenset[str] = frozenset(WRITE_OPERATIONS)


def _bucket_target_tool(target_tool: str | None) -> str:
    """Pass-through for allowlisted write ops, ``"other"`` for unknown
    pod-supplied names, ``""`` for None. Parallel to
    ``_bucket_auto_approval_tools`` on the ``ask_ollie_completed`` event."""
    if target_tool is None:
        return ""
    return target_tool if target_tool in _KNOWN_TARGET_TOOLS else "other"


def _analytics_for_audit() -> Any:
    # Lazy import — audit.py is imported by ask_ollie at import time and
    # analytics depends on Settings; keep this circular-safe.
    from opik_mcp.analytics import get_analytics

    return get_analytics()


_audit_logger = logging.getLogger("opik_mcp.audit")
# Audit rows are the only safety net under always-on YOLO (see ADR 0005). They
# MUST emit regardless of global log level — `OPIK_MCP_LOG_LEVEL=WARNING` (a
# perfectly normal production setting) would otherwise silently drop the
# `logger.info(...)` below without raising, so the try/except in ask_ollie
# would never see a "failure" and would proceed to POST `decision="yes"` with
# no record. Pin the audit logger's own level to INFO and attach a dedicated
# handler so the row survives any parent-level filtering.
_audit_logger.setLevel(logging.INFO)
if not any(getattr(h, "_opik_mcp_audit_owned", False) for h in _audit_logger.handlers):
    _audit_handler = logging.StreamHandler()
    _audit_handler.setLevel(logging.INFO)
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_handler._opik_mcp_audit_owned = True  # type: ignore[attr-defined]
    _audit_logger.addHandler(_audit_handler)


class AuditRow(BaseModel):
    event: str
    workspace: str
    session_id: str
    tool: str
    target_tool: str | None
    tool_use_id: str
    summary: str | None
    input: dict[str, Any]
    auto_approved: bool
    # Stamped at row-construction time, not POST time — Phase 2's hosted ingest
    # may retry or queue the payload, and the audit timeline should reflect when
    # the decision was made, not when persistence succeeded.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def write_auto_approval(
    *,
    workspace: str,
    session_id: str,
    tool_use_id: str,
    target_tool: str | None,
    summary: str | None,
    input: dict[str, Any],
    mcp_session: Any = None,
) -> AuditRow:
    """Record that `opik-mcp` auto-approved a pod ``confirm_required`` event.

    Returns the constructed row so callers (and tests) can introspect it
    without re-parsing the log line.

    ``mcp_session`` is the host-side ServerSession (``ctx.session`` in
    ask_ollie). Threaded through so the analytics emit stamps the same
    env-cohort + bucketed-host block as ``tool_called`` /
    ``ask_ollie_completed``. ``None`` is safe — ``call_context_props``
    falls back to the ``other``/``unknown`` defaults so the schema is
    stable.
    """
    row = AuditRow(
        event="ollie_write_auto_approved",
        workspace=workspace,
        session_id=session_id,
        tool="ask_ollie",
        target_tool=target_tool,
        tool_use_id=tool_use_id,
        summary=summary,
        input=input,
        auto_approved=True,
    )
    _audit_logger.info("audit %s", row.model_dump_json())
    try:
        props = {
            "tool": "ask_ollie",
            "target_tool": _bucket_target_tool(target_tool),
            "had_summary": str(summary is not None).lower(),
        }
        props.update(call_context_props(mcp_session))
        _analytics_for_audit().track_event(EVENT_AUTO_APPROVAL, props)
    except Exception:
        # Audit row is the source of truth; analytics is a secondary signal.
        # Never let analytics fail the auto-approval write.
        _audit_logger.debug("auto_approval analytics emit failed", exc_info=True)
    return row

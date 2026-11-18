from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from opik_mcp.analytics import EVENT_FEEDBACK_SENT, get_analytics
from opik_mcp.config import Settings
from opik_mcp.feedback.redaction import redact
from opik_mcp.identity.context import inbound_authorization


@dataclass(frozen=True)
class FeedbackReport:
    category: str
    impact: str
    tool: str
    target: str
    description: str
    is_requested_by_user: bool


@dataclass(frozen=True)
class SentFeedback:
    feedback_id: str
    removed_count: int

    def answer(self) -> str:
        removed = (
            f" Removed {self.removed_count} value(s) that looked like secrets or personal data."
            if self.removed_count
            else ""
        )
        return f"Feedback queued for the Opik team. id={self.feedback_id}.{removed}"


_BEARER_PREFIX = re.compile(r"(?i)^\s*bearer\s+")


def _known_secrets(settings: Settings) -> list[str]:
    header = _BEARER_PREFIX.sub("", inbound_authorization.get() or "").strip()
    return [secret for secret in (settings.opik_api_key or "", header) if secret]


def send_feedback_report(
    report: FeedbackReport,
    *,
    host_properties: dict[str, str],
    settings: Settings,
) -> SentFeedback:
    """Queues the report; the analytics worker posts it, so the call never waits on the endpoint."""
    redacted = redact(report.description.strip(), known_secrets=_known_secrets(settings))
    feedback_id = str(uuid.uuid4())
    properties = {
        **host_properties,
        "feedback_id": feedback_id,
        "category": report.category,
        "impact": report.impact,
        "tool": report.tool,
        "target": report.target,
        "requested_by_user": str(report.is_requested_by_user).lower(),
        "description": redacted.text,
        "redacted_count": str(redacted.removed_count),
    }
    get_analytics().track_event(EVENT_FEEDBACK_SENT, properties)
    return SentFeedback(feedback_id=feedback_id, removed_count=redacted.removed_count)

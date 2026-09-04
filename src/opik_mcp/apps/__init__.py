"""MCP Apps (``io.modelcontextprotocol/ui``) — the panel behind the ``review`` tool.

The extension is two things on the wire: a tool carrying ``_meta.ui.resourceUri``,
and a ``ui://`` resource served as ``text/html;profile=mcp-app``. FastMCP 1.x can
express both (``meta=`` on ``add_tool``/``resource``), so this needs no SDK upgrade,
and hosts that don't negotiate Apps keep getting exactly the text they got before.

The reference hangs off ``review``, not ``read``: ``read`` is generic over every
entity, and the panel only has views for threads and annotation queues (see ADR 0004
"Amendment — review"). One deliberate model-facing tool, no panel where there is no
view for it.

The app talks back through the tools that already exist. Data comes from
``app_data`` (app-only: ``_meta.ui.visibility = ["app"]``, so it never enters the
model's tool list or its context), and every mutation goes through the universal
``write`` tool — same operations, same validation, same audit trail as when the
model writes.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from opik_mcp.apps.review_html import REVIEW_HTML
from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.read_tool import fetch_entity

UI_URI: Final = "ui://opik/review.html"
APP_MIME_TYPE: Final = "text/html;profile=mcp-app"

#: The only entity types the panel has a view for. ``review`` rejects anything else
#: and points the caller at ``read`` — a generic JSON card would be a worse answer
#: than a plain text read.
APP_ENTITY_TYPES: Final = ("thread", "annotation_queue")

#: ``_meta`` for a model-facing tool that opens the app.
UI_TOOL_META: Final[dict[str, Any]] = {"ui": {"resourceUri": UI_URI}}

#: ``_meta`` for the app's private data channel — surfaced to the app only.
APP_TOOL_META: Final[dict[str, Any]] = {"ui": {"resourceUri": UI_URI, "visibility": ["app"]}}

#: No CSP relaxations: the document inlines its styles, script and logo, and asks
#: for no fonts, so it needs nothing from the deny-by-default policy. The card draws
#: its own border, so the host is asked not to add another (``prefersBorder`` is the
#: spec's camelCase key — hosts ignore anything else).
_RESOURCE_META: Final[dict[str, Any]] = {"ui": {"prefersBorder": False}}


def _ui_root(settings: Settings) -> str:
    base = settings.opik_url or f"{settings.comet_url_override.rstrip('/')}/opik/api"
    return base[:-4].rstrip("/") if base.endswith("/api") else base.rstrip("/")


def thread_url(settings: Settings, project_id: str | None, thread_id: str) -> str | None:
    """Deep link to a thread in the Opik UI (mirrors the frontend's own builder)."""
    if not project_id or not settings.comet_workspace:
        return None
    return (
        f"{_ui_root(settings)}/{settings.comet_workspace}/projects/{project_id}"
        f"/traces?tab=logs&logsType=threads&thread={thread_id}"
    )


def queue_url(settings: Settings, queue_id: str) -> str | None:
    """The SME review link Opik hands to human reviewers."""
    if not settings.comet_workspace:
        return None
    return f"{_ui_root(settings)}/{settings.comet_workspace}/sme?queueId={queue_id}"


#: Feedback-score ``source`` values that mean "a rule wrote this, not a person".
AUTO_SCORE_SOURCES: Final = frozenset({"online_scoring"})


def score_summary(item: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """An item's review state: the queue's scores that were set, and by whom.

    Mirrors the frontend's ``isItemProcessedByUser`` / ``getDistinctAnnotatorCount``
    (``lib/annotation-queues.ts``): a score counts as the human's verdict only if it
    is one the queue asked for. Scores an online evaluation rule wrote are kept
    apart in ``auto_scores`` — they must not mark the item reviewed, and the panel
    shows them beside the controls so the reviewer can agree or overrule.
    """
    scores: dict[str, Any] = {}
    auto: dict[str, Any] = {}
    reviewers: set[str] = set()
    for score in item.get("feedback_scores") or []:
        if names and score.get("name") not in names:
            continue
        if score.get("source") in AUTO_SCORE_SOURCES:
            auto[score.get("name")] = score.get("value")
            continue
        scores[score.get("name")] = score.get("value")
        by_author = score.get("value_by_author") or {}
        reviewers.update(by_author.keys())
        if not by_author and score.get("last_updated_by"):
            reviewers.add(score["last_updated_by"])
    return {
        "scores": scores,
        "auto_scores": auto,
        "reviewers": sorted(reviewers),
        "reviewed": bool(scores),
    }


def _thread_envelope(settings: Settings, fetched: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    thread = fetched.data.get("thread") or {}
    project_id = fetched.project_id or thread.get("project_id")
    envelope["thread_id"] = thread.get("id") or fetched.entity_id
    envelope["project"] = {"id": project_id, "name": fetched.project_name}
    envelope["url"] = thread_url(settings, project_id, envelope["thread_id"])
    return envelope


def _queue_envelope(settings: Settings, fetched: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    queue = fetched.data.get("queue") or {}
    names = queue.get("feedback_definition_names") or []
    envelope["items"] = [
        {
            "thread_id": item.get("id"),
            "number_of_messages": item.get("number_of_messages"),
            "status": item.get("status"),
            "start_time": item.get("start_time"),
            "feedback_scores": item.get("feedback_scores"),
            **score_summary(item, names),
        }
        for item in fetched.data.get("items") or []
    ]
    envelope["url"] = queue_url(settings, str(queue.get("id") or fetched.entity_id))
    return envelope


#: Per-entity envelope shaping, keyed the same way the read registry is keyed so the
#: two can't drift into different entity vocabularies.
_ENVELOPES: Final[dict[str, Any]] = {
    "thread": _thread_envelope,
    "annotation_queue": _queue_envelope,
}


async def build_app_payload(
    client: OpikReadClient,
    settings: Settings,
    *,
    entity_type: str,
    entity_id: str,
    project_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Full-fidelity payload for the iframe.

    Goes through ``fetch_entity`` — the same resolve-and-fetch path ``read`` uses —
    so a pasted Opik link or an entity name means the same thing in the panel as it
    does in text. The only difference is what happens next: the model's answer is
    compressed to a token budget, the human's is not.
    """
    fetched = await fetch_entity(
        entity_type,
        entity_id,
        project_id=project_id,
        project_name=project_name,
        settings=settings,
        client=client,
    )
    envelope: dict[str, Any] = {
        "entity_type": fetched.entity_type,
        "id": fetched.entity_id,
        **fetched.data,
    }
    shape = _ENVELOPES.get(fetched.entity_type)
    return shape(settings, fetched, envelope) if shape else envelope


def register(mcp: FastMCP) -> None:
    """Register the ``ui://`` resource. Called once at import time from server.py."""

    @mcp.resource(
        UI_URI,
        name="opik-review-app",
        title="Opik review",
        description=(
            "Interactive review surface behind the review() tool: conversation "
            "transcripts and annotation queues, with scoring, comments and thread "
            "lifecycle."
        ),
        mime_type=APP_MIME_TYPE,
        meta=_RESOURCE_META,
    )
    def review_app() -> str:
        return REVIEW_HTML


__all__ = [
    "APP_ENTITY_TYPES",
    "APP_MIME_TYPE",
    "APP_TOOL_META",
    "AUTO_SCORE_SOURCES",
    "REVIEW_HTML",
    "UI_TOOL_META",
    "UI_URI",
    "build_app_payload",
    "queue_url",
    "register",
    "score_summary",
    "thread_url",
]

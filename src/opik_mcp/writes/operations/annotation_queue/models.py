"""The input models of ``annotation_queue.create`` and ``.update``.

Apart from their hooks in ``__init__.py``: mypy reads every Pydantic model as
explicit Any, and the hooks are typed strictly.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import Field, model_validator

from opik_mcp.writes.models import _ClientIdMixin, _RequiredProjectMixin, _StrictBase

#: The view kit the backend accepts, ``LATEST_KIT_VERSION`` in opik-backend's
#: ``AnnotationQueueAnnotatorView``; ``opik-annotate/references/kit-v1.md``
#: documents this version.
KIT_VERSION: Final = 1

QueueText = Annotated[str | None, Field(default=None)]
QueueScoreNames = Annotated[
    list[Annotated[str, Field(min_length=1)]] | None,
    Field(default=None, description="Feedback definitions annotators score, by name."),
]
AnnotatorsPerItem = Annotated[int | None, Field(default=None, ge=1, le=1000)]
LockTimeoutSeconds = Annotated[int | None, Field(default=None, ge=1, le=3600)]


class AnnotatorView(_StrictBase):
    """A custom view annotators see in place of the default trace or thread blocks."""

    html: str = Field(
        min_length=1,
        max_length=200_000,
        description=(
            "One HTML document (inline <script>/<style>, no network) rendered in a "
            "sandboxed frame; read_skill('opik-annotate') has what it can rely on."
        ),
    )
    kit_version: int = Field(default=KIT_VERSION, ge=1, le=KIT_VERSION)
    summary: str | None = Field(
        default=None, max_length=1_000, description="What annotators see, in a sentence or two."
    )


class AnnotationQueueCreate(_StrictBase, _ClientIdMixin, _RequiredProjectMixin):
    """``POST /v1/private/annotation-queues``: a queue of traces or threads to review."""

    name: str = Field(min_length=1)
    scope: Literal["trace", "thread"]
    description: QueueText
    instructions: QueueText
    comments_enabled: bool | None = None
    feedback_definition_names: QueueScoreNames
    annotators_per_item: AnnotatorsPerItem
    lock_timeout_seconds: LockTimeoutSeconds
    annotator_view: AnnotatorView | None = None


class AnnotationQueueUpdate(_StrictBase):
    """``PATCH /v1/private/annotation-queues/{id}``: only the fields passed change."""

    id: UUID
    name: str | None = Field(default=None, min_length=1)
    description: QueueText
    instructions: QueueText
    comments_enabled: bool | None = None
    feedback_definition_names: QueueScoreNames
    annotators_per_item: AnnotatorsPerItem
    lock_timeout_seconds: LockTimeoutSeconds
    annotator_view: AnnotatorView | None = None
    clear_annotator_view: bool = Field(
        default=False,
        description=(
            "Remove the custom view; annotators see the default one. Not with annotator_view."
        ),
    )

    @model_validator(mode="after")
    def _one_view_change(self) -> AnnotationQueueUpdate:
        if self.clear_annotator_view and self.annotator_view is not None:
            raise ValueError(
                "view_conflict: pass annotator_view to set the view or "
                "clear_annotator_view to remove it, not both."
            )
        return self

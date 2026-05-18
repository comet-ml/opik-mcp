"""OAuth scope names for write operations (spec §5).

The names mirror Comet/Opik's existing permission grammar. Stage 3 of the
dispatcher checks each operation's required scope against the scopes carried
on the active session. In Phase 1 there is no OAuth wiring yet, so the server
hands the dispatcher ``ALL_WRITE_SCOPES`` and every call passes Stage 3; once
real session-bound scopes land, the server will pass the actual set and
nothing in the dispatcher has to change.
"""

from __future__ import annotations

from typing import Final

SCOPE_TRACE_SPAN_THREAD_LOG: Final = "trace_span_thread_log"
SCOPE_TRACE_SPAN_THREAD_ANNOTATE: Final = "trace_span_thread_annotate"
SCOPE_PROMPT_CREATE: Final = "prompt_create"
SCOPE_DATASET_EDIT: Final = "dataset_edit"
SCOPE_EXPERIMENT_CREATE: Final = "experiment_create"


ALL_WRITE_SCOPES: Final[frozenset[str]] = frozenset(
    {
        SCOPE_TRACE_SPAN_THREAD_LOG,
        SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
        SCOPE_PROMPT_CREATE,
        SCOPE_DATASET_EDIT,
        SCOPE_EXPERIMENT_CREATE,
    }
)


__all__ = [
    "ALL_WRITE_SCOPES",
    "SCOPE_DATASET_EDIT",
    "SCOPE_EXPERIMENT_CREATE",
    "SCOPE_PROMPT_CREATE",
    "SCOPE_TRACE_SPAN_THREAD_ANNOTATE",
    "SCOPE_TRACE_SPAN_THREAD_LOG",
]

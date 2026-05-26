"""Typed exceptions for read / list tool argument validation.

The read / list tools surface user-input mistakes (unknown entity_type,
missing parent id, list-only entity used with ``read``) as ``ToolError``
to the MCP host. Pre-taxonomy those raises were bare — no cause chain —
so the analytics wrapper bucketed them as ``unknown / ToolError``.

``EntityArgValidationError`` is the typed cause that every such raise site
now chains through (``raise ToolError(str(err)) from err``). It owns the
``"validation"`` / 400 ClassVars so ``analytics/errors.bucket_exception``
can route the bucket via ``getattr(type(real), "error_kind")``.

We deliberately keep this as a single coarse class rather than per-failure-
mode subclasses: the validation surface is small and stable, every case
answers the same dashboard question ("the caller passed something the tool
can't handle"), and BI's ``cause_type`` already carries the wrapper class
for triage. Future divergence (e.g. a distinct ``entity_not_listable``
bucket) can split the class then; YAGNI today.
"""

from __future__ import annotations

from typing import ClassVar

from opik_mcp.error_kinds import ErrorKind


class EntityArgValidationError(Exception):
    """Caller passed a read/list argument that doesn't validate.

    Raise this as the typed cause of the ``ToolError`` that surfaces the
    failure to the host. The analytics wrapper unwraps the ToolError → this
    class via ``__cause__`` and reads the ClassVars to bucket the event.
    """

    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400


__all__ = ["EntityArgValidationError"]

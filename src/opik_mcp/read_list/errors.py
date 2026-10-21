"""Typed exceptions for read / list tool argument validation.

The read / list tools surface user-input mistakes (unknown entity_type,
missing parent id, list-only entity used with ``read``) as ``ToolError``
to the MCP host. Pre-taxonomy those raises were bare — no cause chain —
so the analytics wrapper bucketed them as ``unknown / ToolError``.

``EntityArgValidationError`` is the typed cause that every such raise site
now chains through (``raise ToolError(str(err)) from err``). It owns the
``"validation"`` / 400 ClassVars so ``analytics/errors.bucket_exception``
can route the bucket via ``getattr(type(real), "error_kind")``.

The entity-argument cases (unknown entity_type, missing parent id) stay on
this one coarse class. The ``list`` search surface (OPIK-8283) subclasses it
per failure kind — ``OQLSyntaxError`` … ``OQLBadValueError`` in ``oql.py``,
``SortError`` in ``sorting.py``, ``WindowError`` in ``window.py`` — because
the analytics wrapper records only exception class names on failure, and
"which kind of filter mistake do agents make" is a question the dashboards
need answered without ever seeing the string that failed.
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

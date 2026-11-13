"""Does this Opik have automations at all?

There is no toggle to read. A backend that predates the feature answers 404 on
the collection routes, and that 404 would otherwise reach the caller as "not
found: check the id", for a call that named no id. So the collection routes
turn it into one sentence that says what is missing, and the write operations
say the same thing for the same answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opik_mcp.client.errors import OpikNotFoundError
from opik_mcp.read_list.errors import EntityArgValidationError

UNAVAILABLE_CODE = "automations_unavailable"

UNAVAILABLE_SENTENCE = (
    "This Opik has no automations yet: it needs a backend version that ships them. "
    "list('online_rule', project_name=…) still shows the project's online evaluation rules."
)


@contextmanager
def missing_feature_refused() -> Iterator[None]:
    """A 404 from a collection route names no record, so it is the feature that is absent."""
    try:
        yield
    except OpikNotFoundError as err:
        raise EntityArgValidationError(UNAVAILABLE_SENTENCE) from err


__all__ = ["UNAVAILABLE_CODE", "UNAVAILABLE_SENTENCE", "missing_feature_refused"]

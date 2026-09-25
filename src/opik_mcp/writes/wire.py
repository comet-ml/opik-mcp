"""The vocabulary an operation uses to describe its own request.

``dispatch`` runs five stages that are the same for every write: look the
operation up, validate the payload, check the scope, send the request,
finalize the response. Everything that differs per operation is a hook the
registry entry carries, and the hooks speak in the types below.

The point is not indirection for its own sake. It is that a new operation's
wire translation, its pre-flight resolves and whatever it wants to say about
its result all live in one module next to its model, instead of as four more
branches in a dispatcher that grows a little less generic each time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

import httpx
from pydantic import BaseModel

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikClient
from opik_mcp.writes.errors import ValidationFailedError, ValidationIssue

if TYPE_CHECKING:  # pragma: no cover - typing only, registry imports operations
    from opik_mcp.writes.registry import WriteOperation


@dataclass(frozen=True)
class WireRequest:
    """One HTTP request, as an operation describes it.

    ``method`` is optional: an operation that does not override it takes the
    registry entry's, which is the usual case.
    """

    path: str
    body: dict[str, Any] | list[Any] = field(default_factory=dict)
    method: str | None = None


@dataclass(frozen=True)
class BuildContext:
    """What a builder knows besides its own items.

    ``prepared`` carries whatever the operation's ``prepare_fn`` resolved (a
    project UUID today), and is ``None`` on a dry run, which never touches the
    network. A builder that needs it must say what a preview looks like
    without it.
    """

    is_batch: bool = False
    prepared: str | None = None
    dry_run: bool = False


#: A rule the payload's model cannot express on its own, checked after
#: Pydantic. Raises ``ValidationFailedError``; returns nothing.
ValidateFn = Callable[..., None]

#: Translate validated models into the request to send. Called on the live
#: path and on a dry run alike, so it must not need a client.
BuildFn = Callable[["WriteOperation", list[BaseModel], BuildContext], WireRequest]

#: Resolve, before the request goes out, an identifier the wire needs but the
#: caller does not carry, or refuse the call. May mutate ``items`` in place
#: (replacing an element) and returns anything the builder needs back.
PrepareFn = Callable[["WriteOperation", list[BaseModel], OpikClient], Awaitable[str | None]]

#: Reinterpret a backend answer that means something other than what it says,
#: returning the request and response to finalize.
RetryFn = Callable[
    ["WriteOperation", OpikClient, WireRequest, httpx.Response],
    Awaitable[tuple[WireRequest, httpx.Response]],
]

#: Add to the success envelope in place: a link to open, a note on what to
#: expect next.
DecorateFn = Callable[
    ["WriteOperation", list[BaseModel], dict[str, Any], Settings, str | None], None
]

#: What a preview cannot show, when it cannot show it.
DryRunNoteFn = Callable[["WriteOperation", list[BaseModel], str | None], str | None]


#: Path segment per annotation target, for the score and comment routes.
TARGET_PATH: Final[dict[str, str]] = {
    "trace": "traces",
    "span": "spans",
    "thread": "traces/threads",
}


def refuse(op: WriteOperation, field: str, message: str, code: str) -> ValidationFailedError:
    """A ``validation_failed`` for a precondition the payload cannot express.

    ``message`` is the one sentence that says what to do, and leads the
    envelope as well as filling the issue.
    """
    return ValidationFailedError.build(
        op.name,
        [ValidationIssue(field, message, code)],
        example=op.example,
        message=message,
    )


def stringify_uuids(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: stringify_uuids(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [stringify_uuids(v) for v in obj]
    if isinstance(obj, UUID):
        return str(obj)
    return obj


def dump(model: BaseModel) -> dict[str, Any]:
    """Strip ``None`` from the model dump using JSON-mode serialization.

    ``mode='json'`` is essential here: it serializes ``datetime`` as ISO-8601
    with the ``T`` separator (and offset suffix) which the Opik Java BE
    requires — Pydantic's default Python-mode dump keeps ``datetime`` objects
    and lets ``json.dumps(default=str)`` stringify them with a space (e.g.
    ``"2026-05-18 18:00:00+00:00"``), which the BE rejects as
    ``DateTimeParseException``.
    """
    dumped: dict[str, Any] = stringify_uuids(model.model_dump(exclude_none=True, mode="json"))
    return dumped


#: MCP ``dataset.create`` type → the backend's ``DatasetType`` value. The two
#: names agree except for the test suite, whose DB value is still the older
#: ``evaluation_suite`` (opik-backend's DatasetType carries a TODO, OPIK-5795,
#: to migrate it to ``test_suite``); when it moves, only this table changes.
DATASET_TYPE_TO_WIRE: Final[dict[str, str]] = {
    "dataset": "dataset",
    "test_suite": "evaluation_suite",
}


def safe_body(resp: httpx.Response) -> Any:
    """The response body as JSON when it is JSON, else the raw text."""
    try:
        return resp.json()
    except (ValueError, httpx.HTTPError):
        return resp.text


__all__ = [
    "DATASET_TYPE_TO_WIRE",
    "TARGET_PATH",
    "BuildContext",
    "BuildFn",
    "DecorateFn",
    "DryRunNoteFn",
    "PrepareFn",
    "RetryFn",
    "ValidateFn",
    "WireRequest",
    "dump",
    "refuse",
    "safe_body",
    "stringify_uuids",
]

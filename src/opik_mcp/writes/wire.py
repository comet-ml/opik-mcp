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

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import BaseModel

from opik_mcp.client.opik import OpikClient
from opik_mcp.config import Settings
from opik_mcp.json_types import JsonObject, JsonValue
from opik_mcp.writes.errors import ValidationFailedError, ValidationIssue

if TYPE_CHECKING:  # pragma: no cover - typing only, registry imports operations
    from opik_mcp.writes.registry import WriteOperation


@dataclass(frozen=True)
class WireRequest:
    """One HTTP request, as an operation describes it.

    ``method`` is optional: an operation that does not override it takes the
    registry entry's, which is the usual case. ``body`` is a ``Mapping`` so an
    operation can describe its wire shape as a TypedDict.
    """

    path: str
    body: Mapping[str, object] = field(default_factory=dict)
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


class ValidateFn(Protocol):
    """A rule the payload's model cannot express on its own, checked after
    Pydantic. Raises ``ValidationFailedError``; returns nothing."""

    def __call__(
        self,
        op: WriteOperation,
        items: list[BaseModel],
        /,
        *,
        is_batch: bool,
        example: JsonObject,
    ) -> None: ...


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
    ["WriteOperation", list[BaseModel], dict[str, object], Settings, str | None], None
]

#: What a preview cannot show, when it cannot show it.
DryRunNoteFn = Callable[["WriteOperation", list[BaseModel], str | None], str | None]


def refuse(op: WriteOperation, field: str, message: str, code: str) -> ValidationFailedError:
    """A ``validation_failed`` for a precondition the payload cannot express.

    ``message`` is the one sentence that says what to do. It leads the
    envelope and is not repeated in the issue, which keeps the field and code.
    """
    return ValidationFailedError.build(
        op.name,
        [ValidationIssue(field, "", code)],
        example=op.example,
        message=message,
    )


def dump(model: BaseModel) -> JsonObject:
    """Strip ``None`` from the model dump using JSON-mode serialization.

    ``mode='json'`` is essential here: it serializes ``datetime`` as ISO-8601
    with the ``T`` separator (and offset suffix) which the Opik Java BE
    requires — Pydantic's default Python-mode dump keeps ``datetime`` objects
    and lets ``json.dumps(default=str)`` stringify them with a space (e.g.
    ``"2026-05-18 18:00:00+00:00"``), which the BE rejects as
    ``DateTimeParseException``. It also renders every ``UUID`` as a string.
    """
    return model.model_dump(exclude_none=True, mode="json")


def safe_body(resp: httpx.Response) -> JsonValue:
    """The response body as JSON when it is JSON, else the raw text."""
    try:
        body: JsonValue = resp.json()
        return body
    except (ValueError, httpx.HTTPError):
        return resp.text


__all__ = [
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
]

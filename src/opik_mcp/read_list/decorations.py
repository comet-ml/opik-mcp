"""The failure convention for a composite read's optional blocks.

A project read is one primary call and four decorations. The primary must fail
the read — there is no useful answer without the record. A decoration must
never fail it, and must never come back looking like data either: a metric
that could not be loaded arriving as a zero is the one outcome worth guarding
against, because "no traces this week" is advice someone may act on.

The exception list is the load-bearing part. Catching only the typed
``Opik*Error``s left ``httpx.TimeoutException`` and ``httpx.HTTPError``
uncaught, so a single slow decoration killed the whole read — and surfaced as
a raw exception rather than a tool error, so the agent lost the record it
already had *and* got no guidance. That is the likeliest failure of a
five-way fan-out, not an exotic one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)

logger = logging.getLogger("opik_mcp.read_list.decorations")

BLOCK_ERRORS: Final[tuple[type[BaseException], ...]] = (
    OpikAuthError,
    OpikNotFoundError,
    OpikValidationError,
    OpikServerError,
    # Everything httpx raises for a connection that never answered: timeouts,
    # resets, DNS, TLS. `HTTPError` is the base of both `TimeoutException` and
    # `TransportError`, so this one entry covers the family.
    httpx.HTTPError,
)


def describe(what: str, exc: BaseException) -> str:
    """The text a failed block carries instead of data.

    ``httpx`` exceptions often stringify to nothing at all (a bare
    ``ReadTimeout`` has an empty message), which would leave the agent an
    error field with no error in it — so the class name stands in.
    """
    detail = str(exc).strip() or type(exc).__name__
    return f"Could not load {what}: {detail}"


async def block[T](
    what: str,
    load: Callable[[], Awaitable[T]],
) -> T | dict[str, Any]:
    """Run one decoration, or return ``{"error": …}`` describing why not.

    Wrapping each leg means a gathered fan-out cannot be brought down by one
    of them: ``asyncio.gather`` without ``return_exceptions`` propagates the
    first failure and discards every sibling's result, so the guard has to sit
    inside each leg rather than around the gather.
    """
    try:
        return await load()
    except BLOCK_ERRORS as exc:
        logger.debug("%s failed: %s", what, exc, exc_info=True)
        return {"error": describe(what, exc)}


__all__ = ["BLOCK_ERRORS", "block", "describe"]

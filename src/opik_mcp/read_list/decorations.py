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

import asyncio
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


DEADLINE_SECONDS: Final = 2.5
"""How long a decoration may hold up the answer it decorates.

Generous against the healthy case — the project read's legs measure 130-500 ms
against production — and it exists because one of them does not stay healthy:
``/activities`` answers in ~250 ms for most projects and 5-11 s for some (the
same projects each time, and with a single row, so it is the shape of the data
rather than its volume). A gathered read finishes with its slowest leg, so
without a deadline one backend query turns a half-second answer into an
eight-second one.

The client's own 30 s timeout still governs the primary fetch. This bounds only
what is optional, which is the whole point: an overview that arrives promptly
missing one block beats a complete one the user gave up waiting for.
"""


async def block[T](what: str, load: Callable[[], Awaitable[T]]) -> T | dict[str, Any]:
    """Run one decoration, or return ``{"error": …}`` describing why not.

    Wrapping each leg means a gathered fan-out cannot be brought down by one
    of them: ``asyncio.gather`` without ``return_exceptions`` propagates the
    first failure and discards every sibling's result, so the guard has to sit
    inside each leg rather than around the gather.

    The deadline is read from :data:`DEADLINE_SECONDS` at call time, not bound
    as a default argument — a module constant used as a default is fixed at
    import and cannot be adjusted afterwards, including by a test that means
    to. It was also a parameter for a while; nothing ever passed it.
    """
    limit = DEADLINE_SECONDS
    try:
        async with asyncio.timeout(limit):
            return await load()
    except TimeoutError:
        logger.debug("%s exceeded its %.1fs deadline", what, limit)
        return {
            "error": (
                f"Could not load {what}: the backend took longer than "
                f"{limit:g}s, so it was left out rather than holding up the "
                "rest of the answer. Retry, or ask for it on its own."
            )
        }
    except BLOCK_ERRORS as exc:
        logger.debug("%s failed: %s", what, exc, exc_info=True)
        return {"error": describe(what, exc)}


__all__ = ["BLOCK_ERRORS", "DEADLINE_SECONDS", "block", "describe"]

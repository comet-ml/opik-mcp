"""Unit tests for :mod:`opik_mcp.analytics.errors`.

The wrapper-level integration tests in ``test_analytics_wrappers.py`` exercise
the same mapping through the decorator surface; this module pins the helper
contract directly so a regression in ``bucket_exception`` / ``bucket_http_status``
/ ``derive_http_status`` fails close to the root cause.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.analytics.errors import (
    bucket_exception,
    bucket_http_status,
    derive_http_status,
)
from opik_mcp.comet_client import (
    CometAuthError,
    CometPermissionError,
    CometProtocolError,
    OllieNotEnabledError,
)
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)


def _pydantic_error() -> PydanticValidationError:
    """Real ``pydantic.ValidationError`` — the class can't be instantiated directly."""

    class _M(BaseModel):
        x: int

    try:
        _M.model_validate({"x": "not-an-int"})
    except PydanticValidationError as e:
        return e
    raise AssertionError("model_validate did not raise — pydantic upgraded?")


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid/")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("synthetic", request=request, response=response)


# --- bucket_http_status -------------------------------------------------- #


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "auth"),
        (403, "permission"),
        (404, "not_found"),
        (408, "timeout"),
        (504, "timeout"),
        # 4xx that is not auth/permission/not-found/timeout → validation.
        (400, "validation"),
        (409, "validation"),
        (422, "validation"),
        (429, "validation"),
        # 5xx → upstream_5xx (excluding 504 above, which is a timeout).
        (500, "upstream_5xx"),
        (502, "upstream_5xx"),
        (503, "upstream_5xx"),
        (599, "upstream_5xx"),
        # 1xx/2xx/3xx and weird codes → unknown.
        (100, "unknown"),
        (200, "unknown"),
        (302, "unknown"),
        (399, "unknown"),
        (600, "unknown"),
        (0, "unknown"),
        (-1, "unknown"),
    ],
)
def test_bucket_http_status_boundaries(status: int, expected: str) -> None:
    assert bucket_http_status(status) == expected


# --- derive_http_status -------------------------------------------------- #


@pytest.mark.parametrize(
    "exc, expected",
    [
        # Subclass MUST resolve to its own status, not the parent's.
        # ``OpikPermissionError`` extends ``OpikAuthError`` — a regression
        # that flipped the table order would mask every 403 as a 401.
        (OpikPermissionError("x"), 403),
        (OpikAuthError("x"), 401),
        (OpikNotFoundError("x"), 404),
        (OpikValidationError("x"), 400),
        (OpikServerError("x"), 500),
        (CometPermissionError("x"), 403),
        (CometAuthError("x"), 401),
        # Exceptions without a canonical status return None — the BI receiver
        # is expected to treat the absent property as "no status known".
        (OllieAuthError("x"), None),
        (OllieStreamError("x"), None),
        (OllieNotEnabledError("x"), None),
        (CometProtocolError("x"), None),
        (PodNotReadyError("x"), None),
        (MissingConfigError("x"), None),
        (httpx.ConnectError("refused"), None),
        (httpx.ReadTimeout("slow"), None),
        (ValueError("x"), None),
    ],
)
def test_derive_http_status_class_lookup(exc: BaseException, expected: int | None) -> None:
    assert derive_http_status(exc) == expected


def test_derive_http_status_reads_httpx_response() -> None:
    """``HTTPStatusError`` carries the wire status on its response."""
    assert derive_http_status(_http_status_error(429)) == 429
    assert derive_http_status(_http_status_error(503)) == 503


# --- bucket_exception ---------------------------------------------------- #


@pytest.mark.parametrize(
    "exc, expected",
    [
        # Permission BEFORE auth — load-bearing subclass ordering.
        (OpikPermissionError("x"), "permission"),
        (OpikAuthError("x"), "auth"),
        (CometPermissionError("x"), "permission"),
        (CometAuthError("x"), "auth"),
        (OllieAuthError("x"), "auth"),
        # 404 / 400 / 500.
        (OpikNotFoundError("x"), "not_found"),
        (OpikValidationError("x"), "validation"),
        (OpikServerError("x"), "upstream_5xx"),
        # Pydantic argument validation lands in the same bucket as Opik's
        # typed validation error — analytics keeps them apart via
        # ``exception_type``.
        (_pydantic_error(), "validation"),
        # Pod warmup timeout — distinct class, same bucket as httpx timeouts.
        (PodNotReadyError("x"), "timeout"),
        # httpx hierarchy — ``TimeoutException`` is the family base; its
        # subclasses (ReadTimeout, ConnectTimeout, etc.) must all land in
        # "timeout", NOT in the broader "network" bucket.
        (httpx.TimeoutException("x"), "timeout"),
        (httpx.ReadTimeout("x"), "timeout"),
        (httpx.ConnectTimeout("x"), "timeout"),
        (httpx.WriteTimeout("x"), "timeout"),
        (httpx.PoolTimeout("x"), "timeout"),
        # Non-timeout RequestErrors → "network".
        (httpx.ConnectError("x"), "network"),
        (httpx.ReadError("x"), "network"),
        (httpx.WriteError("x"), "network"),
        # Our own control-flow errors that don't map cleanly to an upstream
        # taxonomy bucket → "unknown".
        (OllieStreamError("x"), "unknown"),
        (OllieNotEnabledError("x"), "unknown"),
        (CometProtocolError("x"), "unknown"),
        (MissingConfigError("x"), "unknown"),
        # Catch-all.
        (ValueError("x"), "unknown"),
        (RuntimeError("x"), "unknown"),
    ],
)
def test_bucket_exception_class_only(exc: Exception, expected: str) -> None:
    assert bucket_exception(exc) == expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "auth"),
        (403, "permission"),
        (404, "not_found"),
        (422, "validation"),
        (500, "upstream_5xx"),
        (504, "timeout"),
    ],
)
def test_bucket_exception_routes_http_status_errors(status: int, expected: str) -> None:
    """``HTTPStatusError`` is bucketed by its wire status, not by class alone."""
    assert bucket_exception(_http_status_error(status)) == expected


def test_bucket_exception_caller_supplied_status_wins_for_neutral_class() -> None:
    """When the exception class isn't in the typed taxonomy, the caller-supplied
    status takes precedence over the catch-all ``unknown`` bucket. Lets a
    future write tool surface 422-style validation errors via return value."""
    assert bucket_exception(RuntimeError("x"), http_status=422) == "validation"
    assert bucket_exception(RuntimeError("x"), http_status=503) == "upstream_5xx"


def test_bucket_exception_never_reads_args_or_str() -> None:
    """The PRIVACY contract: ``bucket_exception`` must be class-only — the
    coarse bucket cannot depend on free-form message text. Smuggle a payload
    into ``args`` that would change the answer if anyone ever inspected it,
    then assert the answer is still derived from the class."""

    class _Sneaky(ValueError):
        pass

    sneaky = _Sneaky("status_code=403 forbidden permission denied auth failed")
    # If anyone string-matched on the message we'd see "permission" or "auth";
    # class-only must yield "unknown".
    assert bucket_exception(sneaky) == "unknown"


def test_bucket_exception_subclass_resolves_before_parent() -> None:
    """``OpikPermissionError`` extends ``OpikAuthError``. The bucketing layer
    must isinstance-check the specific class first so a 403 doesn't get
    mislabeled as 401 (and the dashboards lose the distinction)."""
    assert bucket_exception(OpikPermissionError("x")) == "permission"
    assert bucket_exception(CometPermissionError("x")) == "permission"

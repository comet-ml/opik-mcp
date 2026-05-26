"""Unit tests for :mod:`opik_mcp.analytics.errors`.

The wrapper-level integration tests in ``test_analytics_wrappers.py`` exercise
the same mapping through the decorator surface; this module pins the helper
contract directly so a regression in ``bucket_exception`` / ``bucket_http_status``
/ ``derive_http_status`` fails close to the root cause.
"""

from __future__ import annotations

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.analytics.errors import (
    bucket_exception,
    bucket_http_status,
    derive_http_status,
    unwrap_to_real_cause,
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
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.uri import InvalidURI
from opik_mcp.writes.errors import (
    AuthorizationDeniedError,
    BackendError,
    BatchTooLargeError,
    UnknownOperationError,
    ValidationFailedError,
    WriteError,
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
        # 408 + 504 both bypass the broad 4xx/5xx arms and land in "timeout";
        # both branches need integration coverage so a regression that drops
        # the explicit ``status in (408, 504)`` check fails loudly here.
        (408, "timeout"),
        (504, "timeout"),
        (422, "validation"),
        (500, "upstream_5xx"),
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


# --- ClassVar contract --------------------------------------------------- #
#
# Each typed exception class owns its ``error_kind`` / ``http_status`` as a
# ClassVar. These tests pin the contract directly so a future class that
# forgets the attribute (or sets the wrong type) regresses the bucketing
# surface in a localized, easy-to-read failure rather than a downstream
# test that just says "expected auth, got unknown".


_TYPED_EXCEPTION_CLASSES: tuple[tuple[type[BaseException], str, int | None], ...] = (
    (OpikAuthError, "auth", 401),
    (OpikPermissionError, "permission", 403),
    (OpikNotFoundError, "not_found", 404),
    (OpikValidationError, "validation", 400),
    (OpikServerError, "upstream_5xx", 500),
    (CometAuthError, "auth", 401),
    (CometPermissionError, "permission", 403),
    (OllieAuthError, "auth", None),
    (OllieStreamError, "unknown", None),
    (OllieNotEnabledError, "unknown", None),
    (CometProtocolError, "unknown", None),
    (PodNotReadyError, "timeout", None),
    (MissingConfigError, "unknown", None),
    # Write-tool envelope: base "unknown" since concrete code never raises bare
    # WriteError; each live subclass shadows the bucket.
    (WriteError, "unknown", None),
    (UnknownOperationError, "validation", 400),
    (ValidationFailedError, "validation", 400),
    (AuthorizationDeniedError, "permission", 403),
    # BackendError ClassVars are fallbacks; the real bucket comes from the
    # instance.extra status — covered by a dedicated test block in Task 2.
    (BackendError, "unknown", None),
    (BatchTooLargeError, "validation", 400),
    (InvalidURI, "validation", 400),
    (EntityArgValidationError, "validation", 400),
    # NOTE: BatchPartialFailureError intentionally omitted — never raised in
    # the codebase. Adding a ClassVar would expose us to a Sentry-firing edge
    # case ("unknown" is not in _USER_SIDE_ERROR_KINDS) for a class that
    # currently produces zero events. Revisit when the first raise site lands.
)


@pytest.mark.parametrize("cls, expected_kind, expected_status", _TYPED_EXCEPTION_CLASSES)
def test_typed_exception_classvars_define_bucket_and_status(
    cls: type[BaseException], expected_kind: str, expected_status: int | None
) -> None:
    """Every typed exception class declares its bucket + status as ClassVars.

    This is what lets ``bucket_exception`` use ``getattr(type(exc), ...)``
    instead of a cascade. A regression here (forgotten attribute, typo in
    the bucket name) localizes the failure to the exception class itself.
    """
    assert cls.error_kind == expected_kind, (  # type: ignore[attr-defined]
        f"{cls.__name__}.error_kind should be {expected_kind!r}"
    )
    assert cls.http_status == expected_status, (  # type: ignore[attr-defined]
        f"{cls.__name__}.http_status should be {expected_status!r}"
    )


def test_subclass_classvar_shadows_parent() -> None:
    """``OpikPermissionError`` extends ``OpikAuthError`` — the subclass's
    ClassVar shadows the parent so analytics returns ``"permission"`` for
    the 403 case. This pins the load-bearing inheritance behavior."""
    # Sanity-check Python's attribute resolution does the right thing.
    assert OpikPermissionError.error_kind == "permission"
    assert OpikAuthError.error_kind == "auth"
    # Same for http_status.
    assert OpikPermissionError.http_status == 403
    assert OpikAuthError.http_status == 401
    # And — most importantly — the bucketing surface honors the override.
    assert bucket_exception(OpikPermissionError("x")) == "permission"
    assert derive_http_status(OpikPermissionError("x")) == 403


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


# --- unwrap_to_real_cause ------------------------------------------------ #


def _raise_chain(*excs: BaseException) -> BaseException:
    """Build a real ``raise X from Y`` chain by raising sequentially.

    Returns the outermost exception with ``__cause__`` populated as Python
    would set it on the wire. Using a real ``raise`` (vs. setting attributes
    by hand) is the only way to also exercise ``__suppress_context__`` and
    keeps the test honest about how chains are actually produced.
    """
    if not excs:
        raise AssertionError("at least one exception required")
    if len(excs) == 1:
        return excs[0]
    outer, *rest = excs
    inner = _raise_chain(*rest)
    try:
        raise outer from inner
    except BaseException as e:
        return e


def test_unwrap_returns_leaf_when_no_wrapper() -> None:
    """A non-wrapper exception has nothing to unwrap — return as-is."""
    leaf = OpikAuthError("x")
    assert unwrap_to_real_cause(leaf) is leaf


def test_unwrap_follows_cause_through_tool_error() -> None:
    """The exact shape every read/list/write tool produces in prod:
    ``raise ToolError(...) from OpikAuthError(...)``. The unwrap must
    surface the real cause so analytics can bucket it as ``auth``."""
    chain = _raise_chain(ToolError("user-facing"), OpikAuthError("401"))
    real = unwrap_to_real_cause(chain)
    assert isinstance(real, OpikAuthError)


def test_unwrap_follows_cause_through_ollie_stream_error() -> None:
    """``OllieStreamError`` is the wrapper at the ``ask_ollie`` boundary; an
    underlying ``httpx.HTTPStatusError`` must be reachable through it."""
    request = httpx.Request("GET", "https://example.invalid/")
    response = httpx.Response(503, request=request)
    inner = httpx.HTTPStatusError("synthetic", request=request, response=response)
    chain = _raise_chain(OllieStreamError("stream died"), inner)
    real = unwrap_to_real_cause(chain)
    assert isinstance(real, httpx.HTTPStatusError)
    assert real.response.status_code == 503


def test_unwrap_follows_context_when_no_explicit_cause() -> None:
    """A bare ``raise ToolError(...)`` inside an ``except OpikAuthError`` block
    sets ``__context__`` (not ``__cause__``). Python's traceback display
    still surfaces the implicit chain — so must we."""
    try:
        try:
            raise OpikNotFoundError("404")
        except OpikNotFoundError:
            # Deliberately omitting ``from …`` — that's the whole point of
            # this test: verify the unwrap still finds the cause via the
            # implicit ``__context__`` slot Python sets.
            raise ToolError("not found")  # noqa: B904
    except ToolError as e:
        real = unwrap_to_real_cause(e)
        assert isinstance(real, OpikNotFoundError)


def test_unwrap_respects_suppress_context() -> None:
    """``raise X from None`` sets ``__suppress_context__`` — the user is
    explicitly saying "don't chain me". Honor that: stop at the wrapper."""
    try:
        try:
            raise OpikAuthError("401")
        except OpikAuthError:
            raise ToolError("opaque") from None
    except ToolError as e:
        real = unwrap_to_real_cause(e)
        # Must remain the wrapper — the implicit context was suppressed.
        assert real is e


def test_unwrap_prefers_explicit_cause_over_implicit_context() -> None:
    """When both ``__cause__`` and ``__context__`` are set (the explicit
    ``raise X from Y`` inside an ``except Z`` block), ``__cause__`` wins —
    Python's own ``__suppress_context__`` machinery establishes this contract.
    """
    try:
        try:
            raise OpikValidationError("validation")  # becomes __context__
        except OpikValidationError:
            raise ToolError("wrapper") from OpikAuthError("auth")  # becomes __cause__
    except ToolError as e:
        real = unwrap_to_real_cause(e)
        # Cause (OpikAuthError) wins over context (OpikValidationError).
        assert isinstance(real, OpikAuthError)


def test_unwrap_walks_multi_layer_chain() -> None:
    """Nested wrappers — e.g. an ``ask_ollie`` invocation surfaced as a
    ``ToolError`` somewhere upstream. Unwrap must reach the typed leaf."""
    chain = _raise_chain(
        ToolError("outer"),
        OllieStreamError("middle"),
        OpikServerError("inner"),
    )
    real = unwrap_to_real_cause(chain)
    assert isinstance(real, OpikServerError)


def test_unwrap_stops_at_first_non_wrapper() -> None:
    """An unfamiliar exception class is treated as a leaf, even if its own
    ``__cause__`` would reach something typed. Conservative by design:
    the wrapper list is small and explicit — anything else is opaque."""
    chain = _raise_chain(
        ToolError("outer"),
        RuntimeError("intermediate, NOT in _WRAPPER_CLASSES"),
        OpikAuthError("would-have-been-the-leaf"),
    )
    real = unwrap_to_real_cause(chain)
    # Stops at RuntimeError; OpikAuthError underneath is intentionally ignored.
    assert isinstance(real, RuntimeError)
    assert not isinstance(real, OpikAuthError)


def test_unwrap_handles_cycle_without_infinite_loop() -> None:
    """Defensive: ``__cause__`` is a writable attribute; a pathological
    chain that loops back must not hang the classifier."""
    a = ToolError("a")
    b = ToolError("b")
    a.__cause__ = b
    b.__cause__ = a
    # Bound by max_depth; must terminate. We don't assert which node we
    # land on — only that no infinite loop occurs.
    real = unwrap_to_real_cause(a)
    assert isinstance(real, ToolError)


def test_unwrap_bounded_max_depth() -> None:
    """Chain longer than ``max_depth`` returns the deepest reachable
    exception within the bound — no further walk, no crash."""
    chain = _raise_chain(
        ToolError("0"),
        ToolError("1"),
        ToolError("2"),
        ToolError("3"),
        ToolError("4"),
        OpikAuthError("leaf"),
    )
    # Default max_depth=4: we reach hop 4 (still a ToolError), then stop.
    real = unwrap_to_real_cause(chain)
    assert isinstance(real, ToolError)
    # Explicit larger depth reaches the leaf.
    real_deep = unwrap_to_real_cause(chain, max_depth=10)
    assert isinstance(real_deep, OpikAuthError)


# --- bucket_exception unwraps via wrapper classes ------------------------ #

# Every entry in this matrix mirrors a real production code path:
#   ``raise ToolError(_format_client_error(...)) from e``
# in read_tool/list_tool/write_tool, and the parallel OllieStreamError
# raise-sites in ollie_client.py. Pre-unwrap, all of these landed in
# "unknown / ToolError" in BI — the gap that motivated this whole change.
_WRAPPED_BUCKET_MATRIX = [
    (OpikAuthError("401"), "auth", 401),
    (OpikPermissionError("403"), "permission", 403),
    (OpikNotFoundError("404"), "not_found", 404),
    (OpikValidationError("400"), "validation", 400),
    (OpikServerError("500"), "upstream_5xx", 500),
    (CometAuthError("401"), "auth", 401),
    (CometPermissionError("403"), "permission", 403),
    (PodNotReadyError("warmup"), "timeout", None),
    (httpx.ReadTimeout("slow"), "timeout", None),
    (httpx.ConnectError("refused"), "network", None),
    (OllieAuthError("ppauth"), "auth", None),
    (MissingConfigError("no key"), "unknown", None),
]


@pytest.mark.parametrize("inner, expected_bucket, expected_status", _WRAPPED_BUCKET_MATRIX)
def test_bucket_exception_unwraps_tool_error(
    inner: Exception, expected_bucket: str, expected_status: int | None
) -> None:
    """``ToolError`` is the FastMCP-contract wrapper around every read/list/
    write failure. The classifier must look through it to the real cause."""
    chain = _raise_chain(ToolError("user-facing"), inner)
    assert bucket_exception(chain) == expected_bucket
    assert derive_http_status(chain) == expected_status


@pytest.mark.parametrize("inner, expected_bucket, expected_status", _WRAPPED_BUCKET_MATRIX)
def test_bucket_exception_unwraps_ollie_stream_error(
    inner: Exception, expected_bucket: str, expected_status: int | None
) -> None:
    """``OllieStreamError`` is the wrapper at the ``ask_ollie`` boundary; it
    also wraps real upstream causes (e.g. SSE error frames carrying a
    propagated HTTP status). Same unwrap contract as ``ToolError``."""
    chain = _raise_chain(OllieStreamError("stream died"), inner)
    assert bucket_exception(chain) == expected_bucket
    assert derive_http_status(chain) == expected_status


def test_bucket_exception_unwraps_http_status_error_through_wrapper() -> None:
    """``httpx.HTTPStatusError`` is bucketed by wire status. When wrapped
    in a ToolError, that wire status must still be recovered."""
    request = httpx.Request("GET", "https://example.invalid/")
    response = httpx.Response(429, request=request)
    inner = httpx.HTTPStatusError("rate-limited", request=request, response=response)
    chain = _raise_chain(ToolError("rate limited"), inner)
    # 429 → 4xx that isn't auth/permission/not-found/timeout → validation.
    assert bucket_exception(chain) == "validation"
    assert derive_http_status(chain) == 429


def test_bucket_exception_bare_tool_error_stays_unknown() -> None:
    """A ``ToolError`` raised without a cause (the few legitimate cases in
    read_tool.py — e.g. ``raise ToolError(_format_ambiguous(...))``) has no
    upstream to surface. Bucket stays ``unknown`` — same as before this
    unwrap was added, so dashboards still flag these as real protocol-bug
    candidates rather than silently routing them somewhere wrong."""
    assert bucket_exception(ToolError("bare")) == "unknown"
    assert derive_http_status(ToolError("bare")) is None


def test_bucket_exception_bare_ollie_stream_error_stays_unknown() -> None:
    """Parallel to the bare-ToolError case — bare ``OllieStreamError``
    (e.g. ``ollie_client.py:132`` "POST /sessions returned no session_id")
    has no upstream cause and stays ``unknown``."""
    assert bucket_exception(OllieStreamError("no session_id")) == "unknown"
    assert derive_http_status(OllieStreamError("no session_id")) is None


def test_bucket_exception_unwrap_does_not_read_message() -> None:
    """Privacy regression guard: even with the new ``__cause__`` walk, the
    classifier must not start sniffing exception messages. Plant a wrapper
    whose message would suggest 'auth' if anyone string-matched, and
    confirm the bucket still comes from the (unrelated) cause class."""
    chain = _raise_chain(
        ToolError("401 unauthorized permission denied"),
        ValueError("would-be-unknown"),
    )
    assert bucket_exception(chain) == "unknown"


# --- BackendError instance-status routing -------------------------------- #
#
# Unlike every other typed exception, BackendError's bucket depends on the
# upstream HTTP status it received — that status lives on instance.extra,
# not a ClassVar. The classifier reads it via _instance_http_status, the
# same helper that handles httpx.HTTPStatusError. Privacy contract preserved:
# we read an integer status, never the response body or error message.


def _backend_error(status: int) -> BackendError:
    """Build a real BackendError instance with the given upstream status."""
    return BackendError.build(
        operation="trace.create",
        status=status,
        body={"detail": "synthetic"},
        method="POST",
        path="/v1/private/traces",
    )


@pytest.mark.parametrize(
    "status, expected_bucket, expected_http",
    [
        # status=0 is malformed/absent; routed safely via bucket_http_status → "unknown".
        # Pins the ``is not None`` guard intent so falsy-zero refactors fail loudly.
        (0, "unknown", 0),
        (401, "auth", 401),
        (403, "permission", 403),
        (404, "not_found", 404),
        (408, "timeout", 408),
        (422, "validation", 422),
        (429, "validation", 429),
        (500, "upstream_5xx", 500),
        (502, "upstream_5xx", 502),
        (503, "upstream_5xx", 503),
        (504, "timeout", 504),
    ],
)
def test_backend_error_instance_status_routes_bucket(
    status: int, expected_bucket: str, expected_http: int
) -> None:
    """BackendError(status=X) must bucket the same as bucket_http_status(X)
    and derive_http_status must surface the wire status. The classifier reads
    the instance status BEFORE the ClassVar fallback, so the "unknown"
    ClassVar never wins for an instance with a valid extra payload."""
    exc = _backend_error(status)
    assert bucket_exception(exc) == expected_bucket
    assert derive_http_status(exc) == expected_http


def test_backend_error_through_tool_error_chain() -> None:
    """The production raise path: write_tool wraps BackendError in ToolError
    via ``raise ToolError(we.to_json()) from we``. The unwrap must surface
    the BackendError, and the instance-status branch must route the bucket."""
    chain = _raise_chain(ToolError("backend rejected"), _backend_error(503))
    assert bucket_exception(chain) == "upstream_5xx"
    assert derive_http_status(chain) == 503


def test_invalid_uri_through_tool_error_chain() -> None:
    """read_tool.py raises ``ToolError(str(e)) from InvalidURI(...)`` — the
    unwrap surfaces the typed cause and the ClassVars set the bucket."""
    chain = _raise_chain(ToolError("bad uri"), InvalidURI("opik://nope/x"))
    assert bucket_exception(chain) == "validation"
    assert derive_http_status(chain) == 400


def test_entity_arg_validation_error_through_tool_error_chain() -> None:
    """list_tool / read_tool wrap ``EntityArgValidationError`` in ``ToolError``
    when the caller passes an unknown entity_type or omits a required parent
    id — the unwrap must reach it and bucket as validation/400."""
    chain = _raise_chain(
        ToolError("user-facing"),
        EntityArgValidationError("Cannot list 'wat'. Listable types: ..."),
    )
    assert bucket_exception(chain) == "validation"
    assert derive_http_status(chain) == 400


def test_backend_error_without_extra_status_falls_back_to_classvar() -> None:
    """A BackendError missing the extra payload (e.g. constructed by hand in
    a test, or a future refactor that forgets the status) falls back to the
    ClassVar — "unknown" / None — rather than crashing the classifier."""
    bare = BackendError(operation="trace.create")
    assert bucket_exception(bare) == "unknown"
    assert derive_http_status(bare) is None

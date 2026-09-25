from __future__ import annotations

import copy
import pickle

import pytest

from opik_mcp.writes.errors import (
    AuthorizationDeniedError,
    BackendError,
    BatchPartialFailureError,
    BatchTooLargeError,
    UnknownOperationError,
    ValidationFailedError,
    ValidationIssue,
    WriteError,
)

BUILT_ERRORS: tuple[WriteError, ...] = (
    UnknownOperationError.build("trace.creat", ("trace.create", "span.create")),
    ValidationFailedError.build(
        "trace.create",
        [ValidationIssue("name", "Field required", "missing")],
        expected_schema={"type": "object"},
        example={"name": "t"},
    ),
    AuthorizationDeniedError.build("trace.create", "trace:log"),
    BackendError.build("trace.create", 500, {"errors": ["boom"]}, method="POST", path="/x"),
    BatchTooLargeError.build("trace.create", 1001, 1000),
    BatchPartialFailureError.build("trace.create", [{"index": 0}], [{"index": 1}]),
)


@pytest.mark.parametrize("error", BUILT_ERRORS, ids=lambda error: type(error).__name__)
def test_args_carry_the_constructor_arguments(error: WriteError) -> None:
    assert error.args == (error.operation, error.message, error.extra)


@pytest.mark.parametrize("error", BUILT_ERRORS, ids=lambda error: type(error).__name__)
def test_an_error_survives_pickling_and_copying(error: WriteError) -> None:
    for rebuilt in (pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)):
        assert type(rebuilt) is type(error)
        assert rebuilt.args == error.args
        assert rebuilt.to_json() == error.to_json()

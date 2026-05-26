"""Structured error envelope for the ``write`` tool (spec §4.5).

Errors are returned as MCP tool results with ``isError: true``. The body is
a JSON string in a single text-content block because ``structuredContent``
support across hosts is still uneven as of MCP spec 2025-06-18. The shape
itself is stable so telemetry and recovery harnesses can key off the fields.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Literal

from opik_mcp.error_kinds import ErrorKind

ErrorCode = Literal[
    "validation_failed",
    "unknown_operation",
    "authorization_denied",
    "backend_error",
    "batch_too_large",
    "batch_partial_failure",
]


# Stable codes — used as a closed enum by callers (analytics, recovery
# harnesses). Add cases sparingly; broaden the union before adding new ones.
CODE_VALIDATION_FAILED: Final = "validation_failed"
CODE_UNKNOWN_OPERATION: Final = "unknown_operation"
CODE_AUTHORIZATION_DENIED: Final = "authorization_denied"
CODE_BACKEND_ERROR: Final = "backend_error"
CODE_BATCH_TOO_LARGE: Final = "batch_too_large"
CODE_BATCH_PARTIAL_FAILURE: Final = "batch_partial_failure"


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "message": self.message, "code": self.code}


@dataclass
class WriteError(Exception):
    """Base for all structured write errors. Renders as the JSON body."""

    # Class-level taxonomy used by analytics/errors.bucket_exception. Subclasses
    # shadow these with their own canonical bucket; the base falls through to
    # "unknown" since concrete code never raises bare WriteError.
    error_kind: ClassVar[ErrorKind] = "unknown"
    http_status: ClassVar[int | None] = None

    error: ErrorCode
    operation: str | None = None
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.error}
        if self.operation is not None:
            out["operation"] = self.operation
        if self.message:
            out["message"] = self.message
        out.update(self.extra)
        return out

    def to_json(self) -> str:
        # Stable insertion order so transcripts diff cleanly.
        return json.dumps(self.to_dict(), separators=(",", ":"), default=str)

    def __str__(self) -> str:
        return self.to_json()


@dataclass
class UnknownOperationError(WriteError):
    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400
    error: ErrorCode = field(default=CODE_UNKNOWN_OPERATION, init=False)

    @classmethod
    def build(cls, operation: str, valid: tuple[str, ...]) -> UnknownOperationError:
        extra: dict[str, Any] = {"valid_operations": list(valid)}
        # Closest valid match (Levenshtein-ish via difflib) — lets the
        # model self-correct from typos in one shot instead of bisecting
        # the full list.
        suggestions = difflib.get_close_matches(operation, valid, n=1, cutoff=0.6)
        if suggestions:
            extra["did_you_mean"] = suggestions[0]
        return cls(
            operation=operation,
            message=f"Unknown operation {operation!r}.",
            extra=extra,
        )


@dataclass
class ValidationFailedError(WriteError):
    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400
    error: ErrorCode = field(default=CODE_VALIDATION_FAILED, init=False)

    @classmethod
    def build(
        cls,
        operation: str,
        issues: list[ValidationIssue],
        *,
        expected_schema: dict[str, Any],
        example: dict[str, Any] | list[Any],
    ) -> ValidationFailedError:
        return cls(
            operation=operation,
            extra={
                "issues": [i.to_dict() for i in issues],
                "expected_schema": expected_schema,
                "example": example,
            },
        )


@dataclass
class AuthorizationDeniedError(WriteError):
    error_kind: ClassVar[ErrorKind] = "permission"
    http_status: ClassVar[int | None] = 403
    error: ErrorCode = field(default=CODE_AUTHORIZATION_DENIED, init=False)

    @classmethod
    def build(cls, operation: str, required_scope: str) -> AuthorizationDeniedError:
        return cls(
            operation=operation,
            message=(
                f"Operation {operation!r} requires scope {required_scope!r} "
                "which the current session does not have. Re-consent with the "
                "additional scope and retry."
            ),
            extra={"required_scope": required_scope},
        )


@dataclass
class BackendError(WriteError):
    # ClassVar defaults are intentional fallbacks — the real bucket comes from
    # the upstream HTTP status carried on ``instance.extra["backend_error"]
    # ["status"]``. analytics/errors._instance_http_status reads that integer
    # BEFORE the ClassVar lookup runs. These defaults exist for defense in
    # depth — a hand-constructed BackendError missing the extra payload
    # collapses safely into "unknown" instead of crashing the classifier.
    error_kind: ClassVar[ErrorKind] = "unknown"
    http_status: ClassVar[int | None] = None
    error: ErrorCode = field(default=CODE_BACKEND_ERROR, init=False)

    @classmethod
    def build(
        cls,
        operation: str,
        status: int,
        body: Any,
        *,
        method: str,
        path: str,
    ) -> BackendError:
        return cls(
            operation=operation,
            message=f"Backend rejected {method} {path} with status {status}.",
            extra={
                "backend_error": {
                    "status": status,
                    "body": body,
                    "method": method,
                    "path": path,
                }
            },
        )


@dataclass
class BatchTooLargeError(WriteError):
    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400
    error: ErrorCode = field(default=CODE_BATCH_TOO_LARGE, init=False)

    @classmethod
    def build(cls, operation: str, size: int, limit: int) -> BatchTooLargeError:
        return cls(
            operation=operation,
            message=f"Batch size {size} exceeds the {limit}-item limit for {operation!r}.",
            extra={"size": size, "limit": limit},
        )


@dataclass
class BatchPartialFailureError(WriteError):
    error: ErrorCode = field(default=CODE_BATCH_PARTIAL_FAILURE, init=False)

    @classmethod
    def build(
        cls,
        operation: str,
        successes: list[dict[str, Any]],
        failures: list[dict[str, Any]],
    ) -> BatchPartialFailureError:
        return cls(
            operation=operation,
            message=(
                f"{len(successes)} item(s) succeeded, {len(failures)} failed — see "
                "successes/failures for the per-index partition."
            ),
            extra={"successes": successes, "failures": failures},
        )


__all__ = [
    "CODE_AUTHORIZATION_DENIED",
    "CODE_BACKEND_ERROR",
    "CODE_BATCH_PARTIAL_FAILURE",
    "CODE_BATCH_TOO_LARGE",
    "CODE_UNKNOWN_OPERATION",
    "CODE_VALIDATION_FAILED",
    "AuthorizationDeniedError",
    "BackendError",
    "BatchPartialFailureError",
    "BatchTooLargeError",
    "ErrorCode",
    "UnknownOperationError",
    "ValidationFailedError",
    "ValidationIssue",
    "WriteError",
]

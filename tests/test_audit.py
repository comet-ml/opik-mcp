import json
import logging

import pytest

from opik_mcp.audit import write_auto_approval


def test_write_auto_approval_emits_log_row(caplog: pytest.LogCaptureFixture) -> None:
    """The function's whole job: emit a JSON audit line on `opik_mcp.audit`."""
    with caplog.at_level(logging.INFO, logger="opik_mcp.audit"):
        row = write_auto_approval(
            workspace="ws",
            session_id="sess-1",
            tool_use_id="tu-1",
            target_tool="add_test_suite_item",
            summary="add an item",
            input={"suite": "s1"},
        )

    audit_records = [r for r in caplog.records if r.name == "opik_mcp.audit"]
    assert len(audit_records) == 1
    record = audit_records[0]
    assert record.levelno == logging.INFO

    # Lazy-formatted: msg is the format string, args[0] is the JSON body.
    # Parsing args[0] directly is more robust than .getMessage() under
    # structlog/json-logger interception that Phase 2 may introduce.
    assert record.msg == "audit %s"
    assert isinstance(record.args, tuple)
    payload = record.args[0]
    assert isinstance(payload, str)
    body = json.loads(payload)
    assert body["event"] == "ollie_write_auto_approved"
    assert body["workspace"] == "ws"
    assert body["session_id"] == "sess-1"
    assert body["tool"] == "ask_ollie"
    assert body["target_tool"] == "add_test_suite_item"
    assert body["tool_use_id"] == "tu-1"
    assert body["summary"] == "add an item"
    assert body["input"] == {"suite": "s1"}
    assert body["auto_approved"] is True
    assert "timestamp" in body  # exact value is wall-clock; presence is the contract

    # Caller gets the same row back without re-parsing — used by ask_ollie's
    # logger.info call site for observability.
    assert row.event == "ollie_write_auto_approved"
    assert row.auto_approved is True


def test_audit_record_survives_parent_warning_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Audit MUST be unkillable by parent log-level config.

    If an operator sets `logging.getLogger("opik_mcp").setLevel(WARNING)` (the
    natural translation of OPIK_MCP_LOG_LEVEL=WARNING), a naive `logger.info`
    on `opik_mcp.audit` would silently produce no record at all. The audit
    module pins its own level to INFO; this test enforces that the record is
    created regardless of parent-level filtering.
    """
    parent = logging.getLogger("opik_mcp")
    original_level = parent.level
    parent.setLevel(logging.WARNING)
    try:
        with caplog.at_level(logging.INFO, logger="opik_mcp.audit"):
            write_auto_approval(
                workspace="ws",
                session_id="sess-1",
                tool_use_id="tu-1",
                target_tool="add_test_suite_item",
                summary="add an item",
                input={"suite": "s1"},
            )
    finally:
        parent.setLevel(original_level)

    audit_records = [r for r in caplog.records if r.name == "opik_mcp.audit"]
    assert len(audit_records) == 1, (
        "audit record was dropped by parent-level filtering — "
        "YOLO invariant (no audit ⇒ no confirm POST) cannot be enforced if "
        "the audit logger silently swallows records"
    )
    assert audit_records[0].levelno == logging.INFO

"""The Diagnostics writes over both transports: issue status and the scan job.

Their hooks are ``src/opik_mcp/writes/operations/diagnostics.py``. Two routes
answer by what the stub holds: a job that exists turns ``enable`` into a 409
the server follows with a PATCH, and a project with no job turns ``trigger``
into a 404 the server reports as "enable it first".
"""

from __future__ import annotations

import pytest

from tests.hermetic.servers import issues, result_json
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import ISSUE_ID, PROJECT_ID, PROJECT_NAME
from tests.hermetic.writes.surface import (
    DIAGNOSTICS,
    Sent,
    Wire,
    WriteCase,
    assert_refused_before_anything_is_sent,
    assert_the_write_lands_and_links,
    operations_of,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]


def _without_job(stub: StubBackend) -> None:
    stub.agent_insights_job = None


CASES: tuple[WriteCase, ...] = (
    WriteCase(
        case_id="agent_insights_issue.resolve",
        operation="agent_insights_issue.resolve",
        data={"issue_id": ISSUE_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/agent-insights/issues/{ISSUE_ID}",
                {"project_id": PROJECT_ID, "status": "resolved"},
            ),
        ),
        # A resolved issue leaves the default page, so the link follows it.
        url_suffix=f"{DIAGNOSTICS}/resolved?issue={ISSUE_ID}",
    ),
    WriteCase(
        case_id="agent_insights_issue.close",
        operation="agent_insights_issue.close",
        data={"issue_id": ISSUE_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/agent-insights/issues/{ISSUE_ID}",
                {"project_id": PROJECT_ID, "status": "closed"},
            ),
        ),
        url_suffix=f"{DIAGNOSTICS}/resolved?issue={ISSUE_ID}",
    ),
    WriteCase(
        case_id="agent_insights_issue.reopen",
        operation="agent_insights_issue.reopen",
        data={"issue_id": ISSUE_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/agent-insights/issues/{ISSUE_ID}",
                {"project_id": PROJECT_ID, "status": "open"},
            ),
        ),
        url_suffix=f"{DIAGNOSTICS}?issue={ISSUE_ID}",
    ),
    WriteCase(
        case_id="agent_insights_job.enable",
        operation="agent_insights_job.enable",
        data={"project_name": PROJECT_NAME},
        sent=(Sent("POST", f"/v1/private/agent-insights/jobs/{PROJECT_ID}", {}),),
        url_suffix=DIAGNOSTICS,
        arrange=_without_job,
    ),
    WriteCase(
        case_id="agent_insights_job.enable-existing",
        operation="agent_insights_job.enable",
        data={"project_name": PROJECT_NAME},
        # The job exists, so the create conflicts and the server flips its
        # status instead: enabling twice is safe.
        sent=(
            Sent("POST", f"/v1/private/agent-insights/jobs/{PROJECT_ID}", {}),
            Sent("PATCH", f"/v1/private/agent-insights/jobs/{PROJECT_ID}", {"status": "enabled"}),
        ),
        url_suffix=DIAGNOSTICS,
    ),
    WriteCase(
        case_id="agent_insights_job.trigger",
        operation="agent_insights_job.trigger",
        data={"project_name": PROJECT_NAME},
        sent=(Sent("POST", f"/v1/private/agent-insights/jobs/{PROJECT_ID}/trigger", {}),),
        url_suffix=DIAGNOSTICS,
        extra_keys=frozenset({"note"}),
    ),
)


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_a_write_sends_its_request_and_answers_with_where_to_look(
    wire: Wire, case: WriteCase
) -> None:
    await assert_the_write_lands_and_links(wire, case)


@pytest.mark.parametrize("operation", operations_of(CASES))
async def test_a_payload_the_operation_does_not_accept_is_refused_before_anything_is_sent(
    wire: Wire, operation: str
) -> None:
    """The ``validation_failed`` envelope carries what the caller needs to fix
    the call in one turn: which field, a working example, the retry call, and
    the ``schema()`` call that returns the schema, which it no longer inlines."""
    await assert_refused_before_anything_is_sent(wire, operation)


async def test_a_scan_of_a_project_without_diagnostics_says_to_enable_it(wire: Wire) -> None:
    """A 404 from the trigger route is a missing prerequisite, and the answer
    names the operation that fixes it."""
    wire.backend.agent_insights_job = None
    async with wire.connect() as session:
        result = await session.call_tool(
            "write",
            {"operation": "agent_insights_job.trigger", "data": {"project_name": PROJECT_NAME}},
        )
    assert result.isError
    envelope = result_json(result)

    (issue,) = issues(envelope)
    assert issue["code"] == "diagnostics_not_enabled"
    # The fix is the envelope's lead sentence, said once, not a copy in the issue.
    assert "message" not in issue
    assert "write('agent_insights_job.enable', …)" in str(envelope["message"])

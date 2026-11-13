"""read('automation_run') and its list: an automation's firings."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    refuse,
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import (
    AUTOMATION_ID,
    AUTOMATION_RUN_ID,
    GATE_AUTOMATION_ID,
    GATE_RUN_ID,
    PENDING_RUN_ID,
    PROJECT_NAME,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [
    ("read", {"id": AUTOMATION_RUN_ID}),
    ("read", {"id": GATE_RUN_ID}),
    ("read", {"id": PENDING_RUN_ID}),
    ("list", {"automation_id": AUTOMATION_ID}),
    ("list", {"automation_id": GATE_AUTOMATION_ID, "status": "pending"}),
    ("list", {"project_name": PROJECT_NAME, "status": "failed"}),
]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "automation_run", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "automation_run", CALLS)


async def test_a_window_is_refused_with_how_to_page_instead(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        refusal = await refuse(
            session, "list", entity_type="automation_run", automation_id=AUTOMATION_ID, since="7d"
        )
    assert "newest first" in refusal

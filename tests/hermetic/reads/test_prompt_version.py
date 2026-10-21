"""list('prompt_version'): a prompt's history, paged."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    retired_addresses,
    text,
)
from tests.hermetic.stub_records import PROMPT_ID

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("list", {"prompt_id": PROMPT_ID})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "prompt_version", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "prompt_version", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [
            await text(session, "list", {"entity_type": "prompt_version", "prompt_id": PROMPT_ID})
        ]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )

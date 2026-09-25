"""The records the stub backend holds: their ids, and each one filled from its fixture.

The payloads themselves are in ``tests/hermetic/fixtures/``. What is here is
the part that is not a payload: which ids the records carry, the id scheme
that lets a row be found again from the id the server sends back, and the few
values that differ per record (a span's index, a turn's clock).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from tests.hermetic.fixtures import load, record

PROJECT_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000001"
PROJECT_NAME = "checkout-agent"
TRACE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000002"
SPAN_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000003"

SUITE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000010"
SUITE_NAME = "support-qa"
OTHER_SUITE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000011"
OTHER_SUITE_NAME = "billing-qa"
EXPERIMENT_A = "0199c6a4-3a4c-7f1e-9d2b-000000000020"
EXPERIMENT_B = "0199c6a4-3a4c-7f1e-9d2b-000000000021"
EXPERIMENT_OTHER_SUITE = "0199c6a4-3a4c-7f1e-9d2b-000000000022"

#: A thread is keyed by a caller-chosen string, not a UUID, which is the whole
#: reason ``read('thread', …)`` cannot be addressed the way a trace is.
THREAD_ID = "checkout-session-8842"
THREAD_TRACE_IDS = (
    "0199c6a4-3a4c-7f1e-9d2b-000000000030",
    "0199c6a4-3a4c-7f1e-9d2b-000000000031",
)
#: The thread's model id. The comment route takes this, not ``THREAD_ID``:
#: ``POST /traces/threads/{id}/comments`` wants the UUID that
#: ``threads/retrieve`` returns as ``thread_model_id``.
THREAD_MODEL_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000032"
PROMPT_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000040"
PROMPT_NAME = "refund-answer"
ISSUE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000050"

#: What the backend stores for a test suite, on the dataset's ``type`` and on
#: the experiment's ``evaluation_method``. Not ``test_suite``: opik-backend's
#: OPIK-5795 plans that rename and has not done it.
TEST_SUITE_METHOD = "evaluation_suite"

#: What opik-backend hands back for a field it cut: a string, because the
#: substring broke the JSON, of exactly the threshold length.
CUT_SPAN_OUTPUT = "y" * 10_001

#: A case's payload carries one value no page can print whole: the reason a
#: dataset item has a read of its own.
_NOTES = "why this case is here. " * 60

#: The ids every fixture may name. A fixture takes only the ones it uses.
_IDS: dict[str, object] = {
    "project_id": PROJECT_ID,
    "project_name": PROJECT_NAME,
    "trace_id": TRACE_ID,
    "thread_id": THREAD_ID,
    "thread_model_id": THREAD_MODEL_ID,
    "prompt_id": PROMPT_ID,
    "prompt_name": PROMPT_NAME,
    "issue_id": ISSUE_ID,
    "suite_id": SUITE_ID,
    "test_suite_method": TEST_SUITE_METHOD,
}


def fill(name: str, /, **values: object) -> dict[str, object]:
    """The fixture ``name`` as a record, with the stub's ids filled in."""
    return record(name, **{**_IDS, **values})


def fill_list(name: str, /, **values: object) -> list[object]:
    """The fixture ``name`` as a list of records, with the stub's ids filled in."""
    payload = load(name, **{**_IDS, **values})
    assert isinstance(payload, list), f"tests/hermetic/fixtures/{name}.json is not a list"
    return payload


# --- the id scheme ------------------------------------------------------------ #


#: The comparison ids are built from the case index so a row can be found from
#: the id the server sends back in a refetch, without the stub keeping state.
def case_id(index: int) -> str:
    return f"0199c6a4-3a4c-7f1e-9d2b-1{index:07d}0000"


def case_index(case: str) -> int | None:
    tail = case.rsplit("-", 1)[-1]
    if len(tail) != 12 or not tail.startswith("1") or not tail.isdigit():
        return None
    return int(tail[1:8])


def run_trace_id(index: int, position: int, run: int) -> str:
    return f"0199c6a4-3a4c-7f1e-9d2b-2{index:07d}{position}{run:03d}"


#: One case the item routes serve, and the trace it was made from. Exported so
#: a test can address a case without re-deriving the id scheme above.
CASE_ID: str = case_id(3)
CASE_TRACE_ID: str = run_trace_id(3, 0, 0)


def _turn_id(index: int) -> str:
    """A turn's trace id. The first two are named so a probe can point at one."""
    if index < len(THREAD_TRACE_IDS):
        return THREAD_TRACE_IDS[index]
    return f"0199c6a4-3a4c-7f1e-9d2b-3{index:07d}0000"


def _span_id(index: int) -> str:
    return SPAN_ID if index == 0 else f"0199c6a4-3a4c-7f1e-9d2b-4{index:07d}0000"


# --- records that differ per index -------------------------------------------- #


def span(index: int = 0) -> dict[str, object]:
    # Only the first span carries the cut body: a read counts the spans that
    # lost bytes, and a tree where every span was cut cannot tell that count
    # from the span count. It also keeps a 200-span tree cheap to serve.
    return fill(
        "span", span_id=_span_id(index), output=CUT_SPAN_OUTPUT if index == 0 else {"ok": True}
    )


def thread_turn(index: int, total: int) -> dict[str, object]:
    """One trace carrying the thread's id: a turn, once the read projects it."""
    # Descending on purpose: the read sorts turns into conversation order
    # itself, and a stub that hands them over already sorted could not tell a
    # working sort from a missing one.
    minutes = total - index
    return fill(
        "thread_turn",
        turn_id=_turn_id(index),
        clock=f"{10 + minutes // 60:02d}:{minutes % 60:02d}",
        # The first turn's body is the one the backend cut, for the same
        # reason only the first span's is.
        input=CUT_SPAN_OUTPUT if index == 0 else {"question": f"turn {index}"},
        index=index,
    )


def thread() -> dict[str, object]:
    """Thread metadata, as both the list page and ``retrieve`` send it."""
    return fill("thread", number_of_messages=len(THREAD_TRACE_IDS))


def prompt_version(index: int) -> dict[str, object]:
    return fill("prompt_version", number=index + 1)


def dataset(dataset_id: str) -> dict[str, object]:
    name = SUITE_NAME if dataset_id == SUITE_ID else OTHER_SUITE_NAME
    return fill("dataset", dataset_id=dataset_id, dataset_name=name)


def dataset_item(index: int) -> dict[str, object]:
    """One case as ``DatasetItem`` serialises it, outside any comparison.

    ``notes`` is the value no page can print whole, which is what a cut and
    the read that lifts it are proved with.
    """
    return fill(
        "dataset_item",
        case_id=case_id(index),
        case_trace_id=run_trace_id(index, 0, 0),
        index=index,
        notes=_NOTES,
    )


def issue_details(issue: dict[str, object]) -> dict[str, object]:
    """One issue plus its per-day rows, whose ``metadata`` carries the traces.

    The example trace ids arrive per report day and repeat across days; the
    read is the thing that dedupes them, so the fixture repeats one on purpose.
    """
    return {**issue, "details": fill_list("issue_details", turn_trace_id=THREAD_TRACE_IDS[0])}


def page(content: Sequence[object], *, total: int | None = None) -> dict[str, object]:
    return {
        "content": list(content),
        "page": 1,
        "size": len(content),
        "total": len(content) if total is None else total,
    }


def page_size(query: dict[str, list[str]], body: dict[str, object] | None = None) -> int:
    """The page size asked for, from wherever this endpoint takes it.

    A collection the caller asked 200 of must come back at 200, not whole:
    the inline limit is the server's, and a stub that ignores it would let
    "up to 200 inlined" pass on a read that inlined everything.
    """
    if body and isinstance(body.get("size"), int):
        return int(str(body["size"]))
    raw = (query.get("size") or [""])[0]
    return int(raw) if raw.isdigit() else 10


def traces(
    query: dict[str, list[str]], body: dict[str, object], *, turns: int
) -> dict[str, object]:
    """The project's traces, or a thread's turns when filtered to one.

    ``read('thread', …)`` fetches the turns by filtering traces on
    ``thread_id``, so a stub that always answers the same single trace would
    let a thread read look right with no turns in it.
    """
    raw = (query.get("filters") or [""])[0] or json.dumps(body.get("filters") or [])
    if THREAD_ID not in raw:
        return page([fill("trace")])
    asked = min(turns, page_size(query, body))
    return page([thread_turn(i, turns) for i in range(asked)], total=turns if asked else 0)


def metric_results(body: dict[str, object], *, recorded: set[str]) -> dict[str, object]:
    """Series shaped by what was asked for, including the quirks.

    A grouped answer is not filled (each group carries only the buckets it
    appeared in, and the first series is not the widest) and ``__others__``
    arrives unaggregated, several points to one bucket. A sub-metric the
    project never recorded charts nothing at all, which is the case that looks
    like a quiet window and is not. The nulls in the duration series are the
    backend's own "nothing here", a different claim from a zero.
    """
    breakdown = body.get("breakdown")
    if isinstance(breakdown, dict) and breakdown:
        sub = breakdown.get("sub_metric")
        if sub is not None and sub not in {*recorded, "p50", "p90", "p99"}:
            return {"results": []}
        return fill("metrics_grouped")
    metric = str(body.get("metric_type"))
    series = fill("metrics", metric_type=metric)
    return {"results": series.get(metric, series["OTHER"])}

"""``experiment`` — one evaluation run over a dataset."""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from opik_mcp.config import Settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikListClient,
    OpikNotFoundError,
    OpikReadClient,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.read_list.columns import has_value
from opik_mcp.read_list.handler import EntityHandler, ListProjection, PageContext
from opik_mcp.read_list.oql import ENUM_VALUES, PARAM_FIELDS
from opik_mcp.read_list.paging import name_candidates

logger = logging.getLogger("opik_mcp.read_list.entities.experiment")


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    record = await client.get_experiment(entity_id)
    return _with_next_step(record, entity_id)


def _with_next_step(record: dict[str, Any], entity_id: str) -> dict[str, Any]:
    """Point at the per-case view from the averages.

    A read of an experiment answers "how did this run do" with means. The next
    question is always "on which cases", and the call that answers it needs
    nothing but this id and another run's — not the dataset, which it resolves
    itself. An agent that does not know the call falls back to reading every
    trace of both runs, which is what this feature exists to stop.
    """
    if not record.get("dataset_id"):
        return record
    experiment_id = record.get("id") or entity_id
    return {
        **record,
        "comparePerCase": (
            "Which cases differ, rather than these averages: "
            f"list('dataset_item', experiment_ids=['{experiment_id}', "
            "'<other experiment id>'])"
        ),
    }


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_experiments(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_experiments(**kw)


#: Always printed, so two pages can be read beside each other. ``id`` and
#: ``name`` come first from the table itself.
#:
#: ``trace_count`` sits immediately left of the scores because it is what
#: makes them safe to act on. Driving the tool, a trial ranked first at 0.634
#: turned out to be a mean over three cases — and it lost one of them. The
#: count is on every record the backend sends; leaving it off was inviting a
#: decision the page could not support.
_SPINE: Final = (
    "type",
    "status",
    "dataset_name",
    "created_at",
    "trace_count",
    "feedback_scores",
)

#: Printed when some row on the page has one, in this order whichever subset
#: shows. Each is null for a whole class of experiment rather than
#: occasionally missing: assertion counts exist only for a suite run with
#: assertions, a prompt version only for a run that linked one, an
#: optimization id only for a trial. Measured on a live optimizer workspace,
#: 32 of 32 rows had neither assertion counts nor a prompt version.
_CONDITIONAL: Final = (
    "assertion_runs",
    "total_estimated_cost",
    "duration.p50",
    "duration.p90",
    "prompt_version",
    "dataset_version",
    "optimization_id",
)

#: The table advertises a filter field by rendering it as a column. These
#: two are not columns on an ordinary page — an experiment nobody optimized
#: has no optimization id — so they are named instead. Read off the
#: compiler's own table, so the hint cannot come to name a field that no
#: longer compiles.
_FILTER_HINT: Final = f"filter: {', '.join(PARAM_FIELDS['experiment'])}."

#: The table's own default. Named rather than widened: an experiment's
#: scores are no longer than a trace's, and the table states every cut it
#: makes, so a wider cell here would be a difference with nothing behind it.
_CELL_LIMIT: Final = 60

#: What the two parameter fields accept, read off the compiler's own tables so
#: the sentence cannot come to describe a filter that no longer compiles.
_FILTER_VALUES: Final = (
    f"type accepts {', '.join(ENUM_VALUES['experiment']['type'])}; "
    "optimization_id takes one run id."
)


async def page_note(client: OpikListClient, _settings: Settings, page: PageContext) -> str | None:
    """Why an empty experiment page is empty — the one place that can say so.

    Two holes, both found by driving the built server rather than reading it.
    A page filtered to a type nobody has answers "No experiments found." in a
    workspace holding thirty-two, and an agent relays that as "you have no
    experiments": a false answer, from a true sentence. And the filter
    vocabulary is advertised by the projection note, which only a page with
    rows under it prints — so the moment the caller most needs the accepted
    values is the one moment nothing names them.

    Returns ``None`` for a page that has rows: the projection note carries the
    hint there, and saying it twice is worse than saying it once.
    """
    if not page.empty:
        return None
    try:
        whole = await client.list_experiments(size=1)
    except (
        OpikAuthError,
        OpikNotFoundError,
        OpikValidationError,
        OpikServerError,
        httpx.HTTPError,
    ):
        # A note decorates an answer the caller already has. A failed lookup
        # here must leave the page as it was, never turn it into an error.
        logger.debug("experiment page note: workspace count lookup failed", exc_info=True)
        return None
    total = whole.get("total")
    if not isinstance(total, int) or total <= 0:
        return "This workspace has no experiments yet."
    plural = "s" if total != 1 else ""
    return f"The workspace has {total} experiment{plural}; none match this query. {_FILTER_VALUES}"


def derive_columns(record: dict[str, Any]) -> dict[str, Any]:
    """The cells an experiment row needs and the record does not hand over.

    Three are facts the backend splits or nests: how many assertion runs
    passed, which prompt version ran, which dataset version it ran against.
    The fourth is a field the record does carry, rounded — see below for why
    that happens here rather than in the renderer.
    """
    derived: dict[str, Any] = {}
    passed, total = record.get("passed_count"), record.get("total_count")
    if passed is not None and total is not None:
        # "assertion runs", not "passed": these are null for any run without
        # assertions, and a zero in a column called passed reads as a failure
        # rather than as a question that does not apply.
        derived["assertion_runs"] = f"{passed}/{total}"
    versions = record.get("prompt_versions")
    if isinstance(versions, list) and versions:
        derived["prompt_version"] = ",".join(_version_label(v) for v in versions)
    summary = record.get("dataset_version_summary")
    if isinstance(summary, dict) and summary.get("version_name"):
        derived["dataset_version"] = summary["version_name"]
    cost = record.get("total_estimated_cost")
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        # Seen live: 5.099999e-06 rendered as 0.000005099999 — eleven digits
        # of float noise in a column someone is scanning to compare spend.
        # Rounded here rather than in the renderer so it stays a number and
        # the table's own expansion of exponents still applies.
        derived["total_estimated_cost"] = float(f"{cost:.6g}")
    return {**record, **derived}


def _version_label(version: Any) -> str:
    """``v3``, or the short commit for a mask, which has no sequential number.

    An empty cell there would read as "no prompt" rather than as "a prompt
    this listing cannot number".
    """
    if not isinstance(version, dict):
        return ""
    number = version.get("version_number")
    if number:
        return str(number)
    commit = version.get("commit") or ""
    return str(commit)[:8]


def project_experiments(items: list[dict[str, Any]]) -> ListProjection:
    """Columns for one page: the spine, plus the conditionals it can fill.

    A fixed table would print two columns of nothing on every row of an
    optimizer workspace, and a table that printed only what every row has
    would drop a suite run's pass counts the moment one trial shared the page.
    The rule is "some row has it", and what that leaves out is named under the
    table — an absent column must never read as a field the records lack.
    """
    present = tuple(column for column in _CONDITIONAL if any(has_value(i, column) for i in items))
    omitted = [column for column in _CONDITIONAL if column not in present]
    note = "Columns are those some row on this page has"
    if omitted:
        note += f"; omitted: {', '.join(omitted)} (no row has them)"
    return ListProjection(
        columns=(*_SPINE, *present),
        cell_limit=_CELL_LIMIT,
        note=f"{note}. {_FILTER_HINT}",
    )


HANDLER = EntityHandler(
    entity_type="experiment",
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    # The listing used to show the dataset, the date and the scores, and drop
    # everything that says whether a comparison between two runs is even
    # valid. All of it arrives in the same response, so dropping it bought
    # nothing and cost a read per row.
    list_row_fn=derive_columns,
    list_projection_fn=project_experiments,
    page_note_fn=page_note,
    # The refusal has to end the caller's problem, not restate it. The
    # backend orders experiments by id descending and the ids are time
    # ordered, so the page is newest-first whether or not anyone asked for
    # it — which is what a window was reaching for. Naming the sort field
    # makes the alternative copyable into the next call.
    no_window_reason=(
        "experiments have no time window on the backend. The list is already newest-first "
        '(sort="created_at desc" to say so explicitly), so page through it until you pass '
        "the date you care about."
    ),
    description="Experiment status + summary scores.",
)

"""``experiment`` — one evaluation run over a dataset."""

from __future__ import annotations

import logging
from typing import Any, Final

from opik_mcp.config import Settings
from opik_mcp.opik_client import (
    OpikListClient,
    OpikReadClient,
)
from opik_mcp.read_list.columns import has_value, resolve
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


def _accepted_values() -> str:
    """What each parameter field takes, read off the compiler's own tables.

    Names the fields from ``PARAM_FIELDS`` rather than spelling them again:
    a third parameter field would otherwise update the hint above and leave
    this sentence quietly describing the wrong two.
    """
    parts: list[str] = []
    for field, spec in PARAM_FIELDS["experiment"].items():
        values = ENUM_VALUES.get("experiment", {}).get(field)
        if values:
            parts.append(f"{field} accepts {', '.join(values)}")
        elif spec.value_form == "uuid":
            parts.append(f"{field} takes one id")
        else:
            parts.append(field)
    return "; ".join(parts) + "."


async def page_note(client: OpikListClient, _settings: Settings, page: PageContext) -> str | None:
    """Why an empty experiment page is empty — the one place that can say so.

    A page filtered to a type nobody has answers "No experiments found." in a
    workspace holding thirty-two, and an agent relays that as "you have no
    experiments": a false answer, from a true sentence. The filter vocabulary
    has the mirror problem — it is advertised by the projection note, which
    only prints under rows, so the moment the caller most needs the accepted
    values is the one moment nothing names them. Both were found by driving
    the built server rather than by reading it.

    So was the third case, which the first version of this note got wrong in
    the same way it was written to fix: an empty page is not always an empty
    result. Paging past the last page returns no rows and a total, and
    "none match" is false there — they do match, on page one. The page's own
    total settles it without asking the backend anything.

    A page with rows gets no vocabulary — the projection note carries it, and
    saying it twice is worse than once — but it may get the one thing a table
    of ranked scores cannot say about itself: see :func:`_ranking_caveat`.
    """
    if not page.empty:
        return _ranking_caveat(page)
    if page.total > 0:
        # Rows exist and this slice is past them. Nothing was filtered out,
        # so there is no count to look up and nothing to explain but the page.
        return f"Page {page.page} is past the end; the listing has {page.total}."
    try:
        whole = await client.list_experiments(size=1)
    except Exception:
        # A note decorates an answer the caller already has, so nothing here
        # may turn an answered page into an error — and "the lookup failed" is
        # not a set of exception types anyone can enumerate correctly: a body
        # of the wrong shape fails as surely as a 500 does.
        logger.debug("experiment page note: workspace count lookup failed", exc_info=True)
        return None
    stock = whole.get("total") if isinstance(whole, dict) else None
    if not isinstance(stock, int) or stock <= 0:
        # "No experiments found." already says this, and says it once.
        return None
    plural = "s" if stock != 1 else ""
    matched = f"The workspace has {stock} experiment{plural}; none match this query."
    # The vocabulary answers "what may I filter by", which only a caller who
    # wrote a filter was asking. A name search gets the count and no lecture.
    return f"{matched} {_accepted_values()}" if page.filtered else matched


#: Sort fields that rank runs by how well they did. A page ordered by one is
#: an invitation to name a winner.
_SCORE_SORTS: Final = ("feedback_scores.", "experiment_scores.", "pass_rate")
#: Below this many cases, a mean is a hint and a gap between two means is
#: not a ranking. Not a statistical threshold — a plain one, chosen so that
#: the runs seen live (one, two, three cases) trip it and a twenty-case
#: evaluation does not.
_THIN_SAMPLE: Final = 10


def _ranking_caveat(page: PageContext) -> str | None:
    """When the page ranks runs by a score over too few cases to trust, say so.

    Driving the tool: sorted by score, a trial stood first at 0.634. It was a
    mean over three cases, and it had lost one of them — the per-case
    comparison said so a call later. I would have named it the winner. The
    table now carries ``trace_count`` beside the score, which lets a careful
    reader notice; this line is for the reader who is about to not notice.
    """
    field = page.sort_field
    if not field or len(page.rows) < 2:
        return None
    if not (field.startswith(_SCORE_SORTS[:2]) or field == _SCORE_SORTS[2]):
        return None
    first, second = page.rows[0], page.rows[1]
    top, runner_up = resolve(first, field), resolve(second, field)
    first_count, second_count = first.get("trace_count"), second.get("trace_count")
    if not isinstance(top, int | float) or not isinstance(runner_up, int | float):
        return None
    if not isinstance(first_count, int) or not isinstance(second_count, int):
        return None
    if min(first_count, second_count) >= _THIN_SAMPLE:
        return None
    gap = abs(float(top) - float(runner_up))
    return (
        f"Ranked by {field}: the top two differ by {gap:.3g} over {first_count} and "
        f"{second_count} cases. A sample that small does not settle a ranking; compare them "
        "case by case (list('dataset_item', experiment_ids=[…])) before naming a winner."
    )


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

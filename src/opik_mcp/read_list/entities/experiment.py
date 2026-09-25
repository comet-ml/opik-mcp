"""``experiment`` — one evaluation run over a dataset."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Final
from urllib.parse import unquote

from opik_mcp.config import Settings
from opik_mcp.opik_client import (
    OpikListClient,
    OpikReadClient,
)
from opik_mcp.read_list.columns import has_value, resolve
from opik_mcp.read_list.handler import (
    EntityHandler,
    ListProjection,
    PageContext,
    ParamField,
    Vocabulary,
)
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.sample import is_thin
from opik_mcp.read_list.ui_links import experiments_compare_url
from opik_mcp.read_list.uri import UriMatch, UriPattern, is_web_link, opik_uri

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
    # Last: the widest column, and the one nobody scans down. Runs are the
    # only listing that cannot share one page-level template, because each
    # names its own project and dataset. Conditional like the rest of this
    # tuple, so a session that cannot build links shows no empty column.
    "url",
)

VOCABULARY = Vocabulary(
    name="experiment",
    filter_fields={
        "metadata": "dictionary",
        "dataset_id": "string",
        "project_id": "string",
        "prompt_ids": "list",
        "tags": "list",
        "feedback_scores": "feedback_scores",
        "experiment_scores": "feedback_scores",
        # Not in ``ExperimentField``: the backend takes these three as query
        # parameters of their own, and ``split_param_clauses`` lifts them out
        # of the compiled array before the call is made. They are declared
        # here because they are the caller's vocabulary either way — how a
        # filter travels is our problem, not theirs.
        "type": "enum",
        "optimization_id": "string",
        "experiment_ids": "string_list",
    },
    # ``ExperimentType``. The resource deserializes the query parameter into
    # this enum and throws on a miss, so an unknown value is a 400 with the
    # parameter echoed back rather than anything the agent can act on. It is
    # also what makes the complement of a negation computable.
    enum_values={"type": ("regular", "trial", "mini-batch", "mutation")},
    param_fields={
        "type": ParamField(
            param="types",
            operators=("=", "in"),
            encoding="json_list",
            why="the backend filters types as a set",
        ),
        "optimization_id": ParamField(
            param="optimization_id",
            operators=("=",),
            encoding="single",
            why="the backend takes one exact id",
            value_form="uuid",
        ),
        # The runs a caller already holds ids for — the two it is about to
        # compare, the five it just ranked — in one call, with every column
        # the listing has. That was N reads or a paged scan before.
        "experiment_ids": ParamField(
            param="experiment_ids",
            operators=("in",),
            encoding="json_list",
            why="the backend takes a set of exact ids to include",
            value_form="uuid",
        ),
    },
    filter_examples=(
        'dataset_id = "<dataset-uuid>" AND tags contains "baseline"',
        'metadata.model = "gpt-4o" AND feedback_scores.accuracy >= 0.8',
    ),
    field_notes={
        "prompt_ids": (
            "matches prompt ids, not prompt version ids: the backend compares against the "
            "experiment's prompt ids, so this narrows to a prompt and not to one version of "
            "it. No prompt-version filter exists on the backend."
        ),
    },
    sort_fields=(
        "id",
        "name",
        "created_at",
        "last_updated_at",
        "created_by",
        "last_updated_by",
        "tags",
        "trace_count",
        "total_estimated_cost",
        "total_estimated_cost_avg",
        "feedback_scores.*",
        "experiment_scores.*",
        "duration.*",
        "pass_rate",
    ),
)


#: Every filterable field, read off the compiler's own table so it cannot name
#: one that no longer compiles. Not just the non-column ones: ``dataset_id`` is
#: filterable while the column is ``dataset_name``.
_FILTER_HINT: Final = (
    f'filter: {", ".join(sorted(VOCABULARY.filter_fields))}. Operators: schema("list.experiment").'
)

#: The table's own default. Named rather than widened: an experiment's
#: scores are no longer than a trace's, and the table states every cut it
#: makes, so a wider cell here would be a difference with nothing behind it.
_CELL_LIMIT: Final = 60


def _accepted_values() -> str:
    """What each parameter field takes, read off the compiler's own tables.

    Names the fields from ``param_fields`` rather than spelling them again:
    a third parameter field would otherwise update the hint above and leave
    this sentence quietly describing the wrong two.
    """
    parts: list[str] = []
    for field, spec in VOCABULARY.param_fields.items():
        values = VOCABULARY.enum_values.get(field)
        if values:
            parts.append(f"{field} accepts {', '.join(values)}")
        elif spec.value_form == "uuid" and spec.encoding == "json_list":
            # Seen live: this said "takes one id" of the field whose whole
            # point is taking several.
            parts.append(f"{field} takes a set of ids, in (…)")
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


#: Sort fields that order runs by how well they did. Two are families keyed
#: by a score name, one is a flat field.
_SCORE_PREFIXES: Final = ("feedback_scores.", "experiment_scores.")
_SCORE_FIELDS: Final = ("pass_rate",)


def _is_score_sort(field: str) -> bool:
    return field.startswith(_SCORE_PREFIXES) or field in _SCORE_FIELDS


def _ranking_caveat(page: PageContext) -> str | None:
    """When the page orders runs by a score over too few cases to trust, say so.

    The incident is the one the ``_SPINE`` comment tells. ``trace_count``
    beside the score lets a careful reader notice; this line is for the reader
    who is about to not notice. It reads the same in either direction — an
    ascending sort puts the worst first, and three cases settle that no better
    than they settle the best.
    """
    field = page.sort_field
    if not field or len(page.rows) < 2 or not _is_score_sort(field):
        return None
    first, second = page.rows[0], page.rows[1]
    lead, next_up = resolve(first, field), resolve(second, field)
    first_count, second_count = first.get("trace_count"), second.get("trace_count")
    if not isinstance(lead, int | float) or not isinstance(next_up, int | float):
        return None
    if not isinstance(first_count, int) or not isinstance(second_count, int):
        return None
    if not is_thin(min(first_count, second_count)):
        return None
    gap = abs(float(lead) - float(next_up))
    return (
        f"Ranked by {field}: the first two rows differ by {gap:.3g} over {first_count} and "
        f"{second_count} cases. A sample that small does not settle an order; compare them "
        "case by case (list('dataset_item', experiment_ids=[…])) before reading it as one."
    )


def row_link(settings: Settings, record: dict[str, Any]) -> str | None:
    """The compare view one row of an experiment listing opens.

    A url per row, which every other listing avoids, because this one has no
    alternative: the address needs the run's project and its dataset, and a
    workspace-wide page has a different pair on every row.
    """
    links = experiment_links(settings, record)
    url = links.get("url")
    return url if isinstance(url, str) else None


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


def experiment_links(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """The compare view this run lives on — an experiment has no page of its own.

    Three things address it and all three are in the record: the project, the
    dataset the view is keyed by, and the run. Any of them missing means no
    link rather than a guessed one.
    """
    project_id = data.get("project_id")
    dataset_id = data.get("dataset_id")
    experiment_id = data.get("id")
    if not all(isinstance(v, str) for v in (project_id, dataset_id, experiment_id)):
        return {}
    url = experiments_compare_url(
        settings,
        project_id=str(project_id),
        dataset_id=str(dataset_id),
        experiment_ids=[str(experiment_id)],
    )
    return {"url": url} if url is not None else {}


# The compare route: dataset in the path, runs as a JSON array in the query.
_WEB_COMPARE_RE = re.compile(r"/experiments/([^/?#]+)/compare")
_WEB_EXPERIMENTS_QS_RE = re.compile(r"[?&]experiments=([^&#]+)")


def compare_link_run(url: str) -> UriMatch | None:
    """The baseline run of a pasted compare link, which is what the address
    bar holds when a user says "here's my experiment": a run has no page of
    its own.

    ``None`` unless the link has the compare path and its ``experiments`` is a
    non-empty JSON array of strings.
    """
    if not is_web_link(url) or _WEB_COMPARE_RE.search(url) is None:
        return None
    runs_query = _WEB_EXPERIMENTS_QS_RE.search(url)
    if runs_query is None:
        return None
    try:
        runs = json.loads(unquote(runs_query.group(1)))
    except ValueError:
        return None
    if not isinstance(runs, list) or not runs:
        return None
    first = runs[0]
    return (first, None) if isinstance(first, str) and first else None


HANDLER = EntityHandler(
    entity_type="experiment",
    is_name_searchable=True,
    uri_patterns=(
        opik_uri("experiments/{id}"),
        UriPattern(match=compare_link_run, is_web_link=True),
    ),
    uri_precedence=1,
    vocabularies=(VOCABULARY,),
    fetch_fn=fetch,
    link_fn=experiment_links,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    # The listing used to show the dataset, the date and the scores, and drop
    # everything that says whether a comparison between two runs is even
    # valid. All of it arrives in the same response, so dropping it bought
    # nothing and cost a read per row.
    list_link_fn=row_link,
    list_row_fn=derive_columns,
    list_projection_fn=project_experiments,
    # The next level down from an experiment row is the prompt it ran, so a
    # projected row keeps its version whether or not it was asked for. Blank
    # for a run that linked no prompt, and then it is not added at all.
    list_identity_fields=("prompt_version", "url"),
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

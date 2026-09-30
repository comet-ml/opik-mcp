from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field

from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.read_list.entities.project_metric.catalog import INTERVALS as METRIC_INTERVALS
from opik_mcp.read_list.entities.project_metric.catalog import METRICS as METRIC_TYPES
from opik_mcp.read_list.list_tool import page_facts, run_list
from opik_mcp.read_list.oql import filter_field_names
from opik_mcp.read_list.registry import VOCABULARIES
from opik_mcp.read_list.sorting import sort_field_label
from opik_mcp.read_list.visibility import DEFAULT_LISTABLE_TYPES
from opik_mcp.server.tools.fields import FIELDS_LIST_DESCRIPTION
from opik_mcp.server.tools.hints import READS


def _list_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    """Analytics labels for ``list``.

    The search surface (OPIK-8283) is recorded as *shape* only: which filter
    fields were used (names, never values or the ``.key`` a score or metadata
    filter carries — those are user vocabulary), which field was sorted on
    (dynamic ``feedback_scores.<name>`` collapses to its prefix), and whether
    a window / free-text search was present. Failed validations don't reach
    this function; they are bucketed by exception class in the wrapper.
    """
    filters = kwargs.get("filters")
    sort = kwargs.get("sort")
    return {
        # What the page itself turned out to be, which the arguments cannot
        # say: an empty page under the sdk default is the shape of "the
        # default hid the traces", and a dashboard needs to see that apart
        # from "there were none".
        **page_facts(),
        "entity_type": kwargs.get("entity_type", ""),
        "had_name_filter": str(kwargs.get("name") is not None).lower(),
        "page": str(kwargs.get("page", 1)),
        "size": str(kwargs.get("size", 25)),
        "has_filters": str(bool(filters)).lower(),
        "filter_fields": ",".join(
            filter_field_names(kwargs.get("entity_type", ""), filters, VOCABULARIES.values())
        ),
        "has_sort": str(bool(sort)).lower(),
        "sort_field": sort_field_label(sort, VOCABULARIES.values()),
        "has_window": str(bool(kwargs.get("since") or kwargs.get("until"))).lower(),
        "has_search": str(bool(kwargs.get("search"))).lower(),
        # Count only — a column name here is a dataset's data key or a score
        # name, which is user vocabulary. See ``_read_props``.
        "field_count": str(len(kwargs.get("fields") or [])),
    }


@instrument_tool("list", props_fn=_list_props)
async def list_entities(
    entity_type: Annotated[
        str,
        Field(
            description=f"One of: {', '.join(sorted(DEFAULT_LISTABLE_TYPES))}.",
            json_schema_extra={"enum": sorted(DEFAULT_LISTABLE_TYPES)},
        ),
    ],
    name: Annotated[
        str | None,
        Field(
            description=(
                "Optional substring filter on entity name. Supported for project, "
                "experiment, prompt, dataset; ignored for sub-collections."
            ),
            max_length=200,
        ),
    ] = None,
    filters: Annotated[
        str | None,
        Field(
            description=(
                "OQL filter for trace, span, thread, experiment, dataset_item, "
                "project_metric: "
                "<field>[.<key>] <op> <value> [AND ...]; ops = != > >= < <= contains "
                "not_contains starts_with ends_with is_empty is_not_empty in not_in; "
                "strings quoted, numbers bare (duration in ms). E.g. "
                "'error_info is_not_empty AND duration > 5000', "
                "'feedback_scores.accuracy < 0.5 AND start_time >= \"2026-09-08T00:00:00Z\"'. "
                'trace/span/thread default to source = "sdk"; a metric is filtered by '
                'the fields of the entity it is about. Reference: schema("list.trace").'
            ),
            max_length=2000,
        ),
    ] = None,
    sort: Annotated[
        str | None,
        Field(
            description=(
                "Sort for trace, span, thread, experiment: '<field> [asc|desc]', desc by "
                "default. E.g. 'duration desc', 'total_estimated_cost', "
                "'feedback_scores.accuracy asc', 'usage.total_tokens'. One field only."
            ),
            max_length=200,
        ),
    ] = None,
    since: Annotated[
        str | None,
        Field(
            description=(
                "Start of the time window for trace, span, thread, project_metric, "
                "agent_insights_issue: "
                "a relative span ('30m', '1h', '24h', '7d') or an ISO-8601 instant with "
                "timezone. Trace/span/thread windows are by record creation time (for an "
                "exact start_time bound use filters); Diagnostics issues aggregate per "
                "report day, so their window is the UTC days it spans and defaults to "
                "all-time, matching the Diagnostics page."
            ),
            max_length=40,
        ),
    ] = None,
    until: Annotated[
        str | None,
        Field(description="End of the time window, same forms as since.", max_length=40),
    ] = None,
    search: Annotated[
        str | None,
        Field(
            description=(
                "Free text for trace, span, thread: matches anywhere in id, name, input, "
                "output, metadata, tags, thread_id (spans: model, provider too). Expensive "
                "on large projects; narrow with since first."
            ),
            max_length=500,
        ),
    ] = None,
    fields: Annotated[
        list[str] | None,
        Field(description=FIELDS_LIST_DESCRIPTION, max_length=50),
    ] = None,
    page: Annotated[
        int,
        Field(description="Page number (1-indexed).", ge=1, le=10_000),
    ] = 1,
    size: Annotated[
        int,
        Field(description="Items per page. Capped at 100.", ge=1, le=100),
    ] = 25,
    project_id: Annotated[
        str | None,
        Field(
            description=(
                "Parent project UUID for project-scoped lists (trace, span, thread, "
                "agent_insights_issue). Pass this OR project_name."
            )
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        Field(
            description=(
                "Parent project name — alternative to project_id for project-scoped "
                "lists; no UUID lookup needed."
            ),
            max_length=200,
        ),
    ] = None,
    dataset_id: Annotated[
        str | None,
        Field(
            description=(
                "Required when listing dataset_items, unless experiment_ids is given. "
                "UUID of the dataset."
            )
        ),
    ] = None,
    experiment_ids: Annotated[
        list[str] | None,
        Field(
            description=(
                "dataset_item: compare these experiments case by case — which cases "
                "regressed, not two averages. First id is the baseline; up to 10. Resolves "
                "the dataset itself; sort and search apply to the runs, and so do filters "
                "(without it they apply to the cases)."
            )
        ),
    ] = None,
    prompt_id: Annotated[
        str | None,
        Field(description="Required when listing prompt_versions. UUID of the prompt."),
    ] = None,
    status: Annotated[
        Literal["open", "resolved", "closed"] | None,
        Field(
            description=(
                "agent_insights_issue only: which Diagnostics issues to list. "
                "Defaults to 'open' (what is broken now); 'resolved' and 'closed' "
                "show issues already dealt with. Ignored for other entity types."
            ),
        ),
    ] = None,
    metric_type: Annotated[
        str | None,
        Field(
            description=(
                "project_metric only: which metric to chart. "
                "Reference: schema('list.project_metric')."
            ),
            json_schema_extra={"enum": sorted(METRIC_TYPES)},
        ),
    ] = None,
    interval: Annotated[
        str | None,
        Field(
            description=(
                "project_metric only: bucket width. Omitted, it follows the window "
                "the way the Metrics tab does: hourly up to 3 days, daily up to 30, "
                "weekly beyond."
            ),
            json_schema_extra={"enum": sorted(METRIC_INTERVALS)},
        ),
    ] = None,
    breakdown: Annotated[
        str | None,
        Field(
            description=(
                "project_metric only: group each bucket by this field, or "
                "'metadata.<key>'. Not valid for every metric — "
                "schema('list.project_metric') says which."
            ),
            max_length=200,
        ),
    ] = None,
    series: Annotated[
        str | None,
        Field(
            description=(
                "project_metric only, with breakdown: which series to group — a "
                "percentile (p50/p90/p99), a score name, or a usage key."
            ),
            max_length=200,
        ),
    ] = None,
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    """List Opik entities with optional filters and pagination.

    Output is a pipe-delimited table with id, name, and a few entity-specific
    columns, plus a pagination footer when more pages exist. When filters
    apply, the first line echoes what was applied. Use read() to get full
    details on any specific item.

    Project-scoped types require their parent:
    - trace: project_id or project_name
    - span: project_id or project_name (searches spans across the project)
    - thread: project_id or project_name
    - agent_insights_issue: project_id or project_name (Diagnostics issues,
      open ones by default; columns: severity, status, total_occurrences,
      latest_count, last_seen)
    - score_name: project_id or project_name (the project's feedback score
      names — trace, span and thread scores together, paged, no id)
    - online_rule: project_id or project_name (the automation rules scoring
      this project's traces)
    - project_metric: project_id or project_name, plus metric_type — one
      metric over time. Rows are time buckets, so page/size/sort are refused.
    - dataset_item: dataset_id, or experiment_ids to compare runs case by case.
      Filter the dataset's own cases on data.<key>, full_data, tags, source,
      trace_id (no sort); read('dataset_item', id) is one case whole
    - prompt_version: prompt_id

    Workspace-wide types (project, experiment, prompt, dataset) accept
    an optional `name` substring filter. trace, span, thread, experiment and
    dataset_item accept an OQL `filters` string; all but dataset_item also
    take a `sort`, and trace, span, thread take a `since`/`until` window and
    free-text `search`.
    """
    if ctx is not None:
        await ctx.info(f"list.called entity_type={entity_type} page={page} size={size}")
    return await run_list(
        entity_type=entity_type,
        name=name,
        filters=filters,
        sort=sort,
        since=since,
        until=until,
        search=search,
        fields=fields,
        page=page,
        size=size,
        project_id=project_id,
        project_name=project_name,
        dataset_id=dataset_id,
        experiment_ids=experiment_ids,
        prompt_id=prompt_id,
        status=status,
        metric_type=metric_type,
        interval=interval,
        breakdown=breakdown,
        series=series,
    )


def register(mcp: FastMCP[object]) -> None:
    mcp.tool(name="list", title="List Opik records", annotations=READS, structured_output=False)(
        list_entities
    )

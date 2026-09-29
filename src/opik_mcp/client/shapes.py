"""The backend's response bodies, as far as this server reads them.

Field names and types follow opik-backend's OpenAPI spec
(``apps/opik-documentation/documentation/fern/openapi/opik.yaml``), the
``*_Public`` view of each schema. Nothing checks them at runtime: the client
parses the body and casts it here, so these are the backend's contract and
not a guarantee, and a reader still narrows a value before trusting it.

Records are ``total=False``. The backend serializes with ``NON_NULL``, so any
field it has no value for is absent rather than null, and the readers treat
every field that way. The page envelope's ``content`` is the one key a
response always carries.

The spec spells some keys in camelCase (``sortableBy``). The wire is snake_case
throughout: ``OpikApplication`` sets the naming strategy on the mapper, and
notes that it does not reach the OpenAPI document.

A record lists the fields some reader looks at, not every field the backend
sends. The rest still arrive, and a read returns them untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NotRequired, ReadOnly, TypedDict

from opik_mcp.json_types import JsonObject, JsonValue


class Page[T](TypedDict):
    """A Spring page: ``TracePage_Public``, ``ExperimentPage_Public`` and the rest.

    ``content`` is read-only so a page of traces is also a page of mappings,
    which is what the readers that do not care about the record take.
    """

    content: ReadOnly[Sequence[T]]
    page: NotRequired[int]
    size: NotRequired[int]
    total: NotRequired[int]
    sortable_by: NotRequired[list[str]]


class ErrorInfo(TypedDict, total=False):
    """``ErrorInfo_Public``."""

    exception_type: str
    message: str
    traceback: str


class FeedbackScore(TypedDict, total=False):
    """``FeedbackScore_Public``: a score on a trace, span or thread."""

    name: str
    category_name: str
    value: float
    reason: str
    source: str
    value_by_author: JsonObject


class FeedbackScoreAverage(TypedDict, total=False):
    """``FeedbackScoreAverage_Public``: an experiment's mean of one score."""

    name: str
    value: float


class ExperimentItemReference(TypedDict, total=False):
    """``ExperimentItemReference_Public``: the experiment a trace ran under."""

    id: str
    name: str
    dataset_id: str
    dataset_item_id: str


class Trace(TypedDict, total=False):
    """``Trace_Public``."""

    id: str
    project_id: str
    name: str
    start_time: str
    end_time: str
    input: JsonValue
    output: JsonValue
    metadata: JsonValue
    tags: list[str]
    error_info: ErrorInfo
    usage: JsonObject
    created_at: str
    last_updated_at: str
    feedback_scores: list[FeedbackScore]
    total_estimated_cost: float
    span_count: int
    duration: float
    thread_id: str
    experiment: ExperimentItemReference
    source: str


class Span(TypedDict, total=False):
    """``Span_Public``."""

    id: str
    project_name: str
    project_id: str
    trace_id: str
    parent_span_id: str
    name: str
    type: str
    start_time: str
    end_time: str
    input: JsonValue
    output: JsonValue
    metadata: JsonValue
    model: str
    provider: str
    usage: JsonObject
    error_info: ErrorInfo
    feedback_scores: list[FeedbackScore]
    total_estimated_cost: float
    duration: float


class TraceThread(TypedDict, total=False):
    """``TraceThread``: one conversation, keyed by ``thread_id`` in a project.

    ``id`` is the caller's ``thread_id`` string; ``thread_model_id`` is the
    UUID the thread comment endpoint takes in its path.
    """

    id: str
    project_id: str
    thread_model_id: str
    start_time: str
    end_time: str
    duration: float
    first_message: JsonValue
    last_message: JsonValue
    feedback_scores: list[FeedbackScore]
    status: str
    number_of_messages: int
    total_estimated_cost: float
    last_updated_at: str


class Project(TypedDict, total=False):
    """``Project_Public``: metadata only; the aggregates are on ``/projects/stats``."""

    id: str
    name: str
    description: str
    created_at: str
    last_updated_at: str
    last_updated_trace_at: str


class KpiMetric(TypedDict, total=False):
    """``KpiMetric``: one Logs-page card, this window against the one before."""

    type: str
    current_value: float
    previous_value: float


class KpiCards(TypedDict, total=False):
    """``KpiCardResponse``."""

    stats: list[KpiMetric]


class DataPoint(TypedDict, total=False):
    """``DataPointNumber_Public``: one bucket of a metric series."""

    time: str
    value: float


class MetricSeries(TypedDict, total=False):
    """``ResultsNumber_Public``: one series of a metric, named by its group."""

    name: str
    data: list[DataPoint]


class ProjectMetrics(TypedDict, total=False):
    """``ProjectMetricResponse_Public``."""

    project_id: str
    metric_type: str
    interval: str
    results: list[MetricSeries]


class ScoreName(TypedDict, total=False):
    """``ScoreName_Public``."""

    name: str
    type: str


class ScoreNames(TypedDict, total=False):
    """``FeedbackScoreNames_Public``."""

    scores: list[ScoreName]


class TokenUsageNames(TypedDict, total=False):
    """``TokenUsageNames``."""

    names: list[str]


class Activity(TypedDict, total=False):
    """``RecentActivityItem_Public``: one entry of a project's activity feed."""

    type: str
    id: str
    name: str
    resource_id: str
    created_by: str
    created_at: str


class AutomationRule(TypedDict, total=False):
    """``AutomationRuleEvaluatorObjectObject_Public``: one online-evaluation rule."""

    id: str
    project_id: str
    name: str
    sampling_rate: float
    enabled: bool
    type: str
    action: str


class PromptVersionLink(TypedDict, total=False):
    """``PromptVersionLink_Public``: the prompt version an experiment ran."""

    prompt_version_id: str
    commit: str
    prompt_id: str
    prompt_name: str


class DatasetVersionSummary(TypedDict, total=False):
    """``DatasetVersionSummary_Public``."""

    id: str
    version_hash: str
    version_name: str


class PercentageValues(TypedDict, total=False):
    """``PercentageValues_Public``."""

    p50: float
    p90: float
    p99: float


class Experiment(TypedDict, total=False):
    """``Experiment_Public``."""

    id: str
    dataset_name: str
    dataset_id: str
    project_id: str
    project_name: str
    name: str
    metadata: JsonValue
    tags: list[str]
    type: str
    evaluation_method: str
    optimization_id: str
    feedback_scores: list[FeedbackScoreAverage]
    experiment_scores: list[FeedbackScoreAverage]
    trace_count: int
    created_at: str
    duration: PercentageValues
    total_estimated_cost: float
    status: str
    prompt_version: PromptVersionLink
    prompt_versions: list[PromptVersionLink]
    dataset_version_id: str
    dataset_version_summary: DatasetVersionSummary
    pass_rate: float
    passed_count: int
    total_count: int


class Dataset(TypedDict, total=False):
    """``Dataset_Public``."""

    id: str
    name: str
    project_id: str
    type: str
    description: str
    created_at: str
    last_updated_at: str
    experiment_count: int
    dataset_items_count: int


class Column(TypedDict, total=False):
    """``Column_Public``: a key some item's payload carries, and its types."""

    name: str
    types: list[str]
    filter_field_prefix: str


class ExperimentItem(TypedDict, total=False):
    """``ExperimentItem_Compare``: one experiment's run of one dataset item."""

    id: str
    experiment_id: str
    dataset_item_id: str
    trace_id: str
    project_id: str
    input: JsonValue
    output: JsonValue
    feedback_scores: list[FeedbackScore]
    total_estimated_cost: float
    duration: float
    assertion_results: list[JsonObject]
    status: str


class DatasetItem(TypedDict, total=False):
    """``DatasetItem_Public``, and ``DatasetItem_Compare`` with its runs attached.

    ``data`` is a JSON object: the spec says ``JsonNode``, the backend's record
    is ``Map<String, JsonNode>``.
    """

    id: str
    trace_id: str
    span_id: str
    source: str
    data: JsonObject
    description: str
    tags: list[str]
    experiment_items: list[ExperimentItem]
    run_summaries_by_experiment: JsonObject
    dataset_id: str
    created_at: str
    last_updated_at: str


class DatasetItemPage(Page[DatasetItem], total=False):
    """``DatasetItemPage_Public``: the page, and the payload keys its items carry."""

    columns: list[Column]


class Columns(TypedDict, total=False):
    """``PageColumns``: the output keys the compared runs carry."""

    columns: list[Column]


class Stat(TypedDict, total=False):
    """``ProjectStatItemObject_Public``: one figure, a number or percentiles by ``type``."""

    name: str
    type: str
    value: JsonValue


class Stats(TypedDict, total=False):
    """``ProjectStats_Public``: the figures over a set of experiment items."""

    stats: list[Stat]


class FeedbackDefinition(TypedDict, total=False):
    """``FeedbackObject_Public``; a categorical one's ``details`` has ``categories``.

    ``categories`` maps each label to the number it is stored as
    (``CategoricalFeedbackDetail_Public``).
    """

    id: str
    name: str
    type: str
    details: JsonObject


class Prompt(TypedDict, total=False):
    """``Prompt_Public``."""

    id: str
    name: str
    project_id: str
    description: str
    template_structure: str
    tags: list[str]
    created_at: str
    last_updated_at: str
    version_count: int


class PromptVersion(TypedDict, total=False):
    """``PromptVersion_Public``."""

    id: str
    prompt_id: str
    commit: str
    version_number: str
    template: str
    metadata: JsonValue
    type: str
    change_description: str
    tags: list[str]
    created_at: str


class AgentInsightsIssue(TypedDict, total=False):
    """``AgentInsightsIssue``: a Diagnostics issue as the listing ranks it."""

    id: str
    name: str
    description: str
    cause: str
    suggested_fix: str
    status: str
    severity: str
    total_occurrences: int
    latest_count: int
    users_impacted: int
    first_seen: str
    last_seen: str


class AgentInsightsIssueDetail(TypedDict, total=False):
    """``AgentInsightsIssueDetail``: one report day of an issue."""

    report_day: str
    count: int
    total_count: int
    users_impacted: int
    total_users: int
    metadata: JsonValue


class AgentInsightsIssueWithDetails(TypedDict, total=False):
    """``AgentInsightsIssueWithDetails``: one issue, with its report days."""

    id: str
    name: str
    description: str
    cause: str
    suggested_fix: str
    status: str
    severity: str
    details: list[AgentInsightsIssueDetail]


class AgentInsightsJob(TypedDict, total=False):
    """``AgentInsightsJob``: a project's Diagnostics scanner."""

    id: str
    project_id: str
    status: str
    last_scan_at: str
    last_failure_reason: str
    last_failure_detail: str
    last_failed_at: str


__all__ = [
    "Activity",
    "AgentInsightsIssue",
    "AgentInsightsIssueDetail",
    "AgentInsightsIssueWithDetails",
    "AgentInsightsJob",
    "AutomationRule",
    "Column",
    "Columns",
    "DataPoint",
    "Dataset",
    "DatasetItem",
    "DatasetItemPage",
    "DatasetVersionSummary",
    "ErrorInfo",
    "Experiment",
    "ExperimentItem",
    "ExperimentItemReference",
    "FeedbackDefinition",
    "FeedbackScore",
    "FeedbackScoreAverage",
    "KpiCards",
    "KpiMetric",
    "MetricSeries",
    "Page",
    "PercentageValues",
    "Project",
    "ProjectMetrics",
    "Prompt",
    "PromptVersion",
    "PromptVersionLink",
    "ScoreName",
    "ScoreNames",
    "Stat",
    "Stats",
    "TokenUsageNames",
    "Trace",
    "TraceThread",
]

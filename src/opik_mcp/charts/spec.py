"""``ChartSpec`` — the agent-facing description of one chart.

This is the model the ``write`` tool's dashboard operations and the
``chart_data`` tool both take, and it is deliberately NOT the shape Opik
stores. The stored shape is a UI widget object (camelCase keys, a layout
entry, a grid position, a ``version`` the frontend migrates); asking an LLM
to author that is asking it to guess at private frontend contracts. Instead
it describes the chart in Opik's own domain terms —

    {"metric": "trace_count", "project_name": "demo", "breakdown": "name"}

— and :meth:`ChartSpec.to_widget` compiles that into the exact widget JSON
the Opik UI renders, while :mod:`charts.query` compiles the same spec into
the ``/metrics`` request that returns its data. One description, two
executions, so a chart an agent can plot is a chart it can also save.

Three widget kinds are supported, matching the dashboard widgets that read
project data: ``metric`` (a time series), ``stat`` (a single-number card) and
``text`` (a markdown note). The experiment widgets are deliberately absent —
they are driven by experiment selection rather than a metric query, so
nothing here could validate or replay them.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opik_mcp.charts.vocabulary import (
    ChartVocabularyError,
    MetricDef,
    check_breakdown,
    check_stat_metric,
    check_sub_metric,
    resolve_metric,
    widget_filter_key,
)

ChartKind = Literal["metric", "stat", "text"]

#: Widget ``type`` values in the stored config, keyed by our ``kind``.
WIDGET_TYPES: dict[ChartKind, str] = {
    "metric": "project_metrics",
    "stat": "project_stats_card",
    "text": "text_markdown",
}


class ChartSpec(BaseModel):
    """One chart, as an agent describes it.

    ``extra='forbid'`` so a plausible-but-wrong key (``metric_type``,
    ``chartType``) fails here with the field list rather than being dropped
    on the floor and rendering an empty widget in the UI.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ChartKind = Field(
        default="metric",
        description=(
            "'metric' = time-series chart, 'stat' = single-number card, 'text' = markdown note."
        ),
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="Widget title. Defaults to a title derived from the metric.",
    )
    metric: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "For kind='metric': a metric name (trace_count, duration, cost, "
            "feedback_scores, span_token_usage, thread_count, …). For "
            "kind='stat': a stat name (trace_count, error_count, duration.p50, "
            "total_estimated_cost_sum, feedback_scores.<name>, …)."
        ),
    )
    chart_type: Literal["line", "bar"] = Field(
        default="line", description="Rendering for kind='metric'."
    )
    source: Literal["traces", "spans"] = Field(
        default="traces", description="Which stats table a kind='stat' card reads."
    )
    project_name: str | None = Field(
        default=None,
        max_length=200,
        description="Project to chart, by name. Resolved to an id when the chart is saved.",
    )
    project_id: UUID | None = Field(
        default=None, description="Project to chart, by UUID. Mutually exclusive with project_name."
    )
    project_ids: list[UUID] | None = Field(
        default=None,
        max_length=50,
        description="Chart several projects at once (workspace aggregation).",
    )
    all_projects: bool = Field(
        default=False,
        description=(
            "Aggregate across every project in the workspace, resolved at render "
            "time — so the chart follows projects added later."
        ),
    )
    breakdown: str | None = Field(
        default=None,
        description=(
            "Split the series by a dimension: name, model, provider, tags, "
            "metadata, type, error_info, error_type, guardrail_name."
        ),
    )
    breakdown_key: str | None = Field(
        default=None,
        max_length=200,
        description="Metadata field to group by when breakdown='metadata'.",
    )
    sub_metric: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Required with a breakdown on multi-series metrics: a percentile "
            "(p50/p90/p99) for duration, the score name for feedback scores, "
            "the usage key for token usage."
        ),
    )
    feedback_scores: list[str] | None = Field(
        default=None,
        max_length=50,
        description="Restrict a feedback-score chart to these score names.",
    )
    filters: list[dict[str, Any]] | None = Field(
        default=None,
        max_length=50,
        description=(
            "Opik filter objects — {field, operator, value, key?} — applied to "
            "the metric's own entity (traces, spans or threads, per the metric)."
        ),
    )
    text: str | None = Field(
        default=None, max_length=10_000, description="Markdown body for kind='text'."
    )

    # --- validation ------------------------------------------------------- #

    @model_validator(mode="after")
    def _validate(self) -> ChartSpec:
        # Vocabulary errors are raised as ValueError so Pydantic folds them
        # into the same ``validation_failed`` envelope as every other field
        # rule — the model never sees two different error shapes for "this
        # chart cannot exist".
        try:
            self._validate_projects()
            if self.kind == "text":
                self._validate_text()
            elif self.kind == "stat":
                self._validate_stat()
            else:
                self._validate_metric()
        except ChartVocabularyError as exc:
            raise ValueError(f"chart_spec_invalid: {exc}") from exc
        return self

    def _validate_projects(self) -> None:
        if self.project_name is not None and self.project_id is not None:
            raise ChartVocabularyError("pass either project_name or project_id, not both.")
        picks = [
            self.project_name is not None or self.project_id is not None,
            self.project_ids is not None,
            self.all_projects,
        ]
        if sum(picks) > 1:
            raise ChartVocabularyError(
                "pick ONE project scope: project_name/project_id, project_ids, or all_projects."
            )
        if self.project_ids is not None and not self.project_ids:
            raise ChartVocabularyError(
                "project_ids is empty — pass at least one id, or all_projects=true "
                "to aggregate over the whole workspace."
            )

    def _validate_text(self) -> None:
        if not self.text:
            raise ChartVocabularyError("kind='text' needs `text` (the markdown body).")
        if self.metric is not None:
            raise ChartVocabularyError("kind='text' takes no metric.")

    def _validate_stat(self) -> None:
        if not self.metric:
            raise ChartVocabularyError(
                "kind='stat' needs `metric` — the stat to display, e.g. 'trace_count'."
            )
        check_stat_metric(self.metric, self.source)
        if self.breakdown is not None:
            raise ChartVocabularyError(
                "a stat card shows one number and cannot be broken down — use "
                "kind='metric' for a split series."
            )

    def _validate_metric(self) -> None:
        if not self.metric:
            raise ChartVocabularyError(
                "kind='metric' needs `metric`, e.g. 'trace_count' or 'duration'."
            )
        metric = resolve_metric(self.metric)
        if self.breakdown is not None:
            check_breakdown(metric, self.breakdown, metadata_key=self.breakdown_key)
            check_sub_metric(metric, self.breakdown, self.sub_metric)
        elif self.breakdown_key is not None:
            raise ChartVocabularyError(
                "breakdown_key is only meaningful with breakdown='metadata'."
            )

    # --- derived ---------------------------------------------------------- #

    @property
    def metric_def(self) -> MetricDef:
        """The resolved metric. Only valid for ``kind='metric'``."""
        assert self.metric is not None  # guaranteed by _validate_metric
        return resolve_metric(self.metric)

    def resolved_title(self) -> str:
        """Title to store — the caller's, or one derived from the chart itself."""
        if self.title:
            return self.title
        if self.kind == "text":
            return "Note"
        assert self.metric is not None
        base = self.metric.replace("_", " ").replace(".", " ").strip().capitalize()
        if self.kind == "metric" and self.breakdown:
            return f"{base} by {self.breakdown.replace('_', ' ')}"
        return base

    def project_scope(self) -> dict[str, Any]:
        """The stored widget's project fields.

        ``projectIds`` is the canonical field in the current dashboard config
        (a one-element list IS a single-project widget); ``allProjects`` is the
        dynamic "every project" signal, which resolves at render time rather
        than freezing today's project list into the config.
        """
        if self.all_projects:
            return {"allProjects": True}
        if self.project_ids:
            return {"projectIds": [str(p) for p in self.project_ids]}
        if self.project_id is not None:
            return {"projectIds": [str(self.project_id)]}
        # Unscoped: legal, and the widget then follows the dashboard's own
        # project selection at view time (project-scoped dashboards, or the
        # runtime picker on a workspace dashboard).
        return {}

    def breakdown_config(self) -> dict[str, Any] | None:
        """Widget-config form of the breakdown (camelCase, as the UI stores it)."""
        if self.breakdown is None:
            return None
        config: dict[str, Any] = {"field": self.breakdown}
        if self.breakdown_key is not None:
            config["metadataKey"] = self.breakdown_key
        if self.sub_metric is not None:
            config["subMetric"] = self.sub_metric
        return config

    # --- compilation ------------------------------------------------------ #

    def to_widget(self, widget_id: str) -> dict[str, Any]:
        """Compile to the widget object stored inside ``dashboard.config``.

        Key casing is the frontend's, not ours: these objects are read by the
        Opik UI, so ``metricType`` / ``chartType`` / ``traceFilters`` are load
        bearing. Empty filter lists are written explicitly because that is what
        the UI's own editor writes, and a missing key reads as "not configured"
        in some widget editors.
        """
        widget: dict[str, Any] = {
            "id": widget_id,
            "title": self.resolved_title(),
            "type": WIDGET_TYPES[self.kind],
        }
        if self.kind == "text":
            widget["config"] = {"content": self.text or ""}
            return widget

        if self.kind == "stat":
            stat_filter_key = "traceFilters" if self.source == "traces" else "spanFilters"
            config: dict[str, Any] = {
                "source": self.source,
                "metric": self.metric,
                **self.project_scope(),
                stat_filter_key: self.filters or [],
            }
            widget["config"] = config
            return widget

        metric = self.metric_def
        config = {
            "metricType": metric.wire,
            "chartType": self.chart_type,
            **self.project_scope(),
            widget_filter_key(metric.family): self.filters or [],
        }
        breakdown = self.breakdown_config()
        if breakdown is not None:
            config["breakdown"] = breakdown
        if self.feedback_scores is not None:
            config["feedbackScores"] = self.feedback_scores
        widget["config"] = config
        return widget

    def with_project_id(self, project_id: str) -> ChartSpec:
        """Copy with ``project_name`` resolved to ``project_id``.

        Name→id resolution needs a backend call, so it happens in the write
        dispatcher (which has a client) rather than in validation (which must
        stay pure and offline for ``dry_run``).
        """
        return self.model_copy(update={"project_id": UUID(project_id), "project_name": None})


ChartSpecList = Annotated[list[ChartSpec], Field(max_length=50)]


__all__ = ["WIDGET_TYPES", "ChartKind", "ChartSpec", "ChartSpecList"]

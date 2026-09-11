"""What ``schema('list.project_metric')`` answers.

Its own file because it is paid for separately. The tool descriptions ride in
every request a host makes; this is fetched only by a caller who is about to
chart something, so the metric table, the grouping matrix and the interval
rule live here at no cost to anyone else.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.read_list.entities.project_metric.catalog import (
    BACKEND_SERIES_CAP,
    DEFAULT_SERIES,
    DEFAULT_WINDOW_DAYS,
    DURATION_PERCENTILES,
    METRICS,
    groupable_by,
)
from opik_mcp.read_list.entities.project_metric.table import OTHERS


def reference() -> dict[str, Any]:
    """What ``schema('list.project_metric')`` answers.

    The metric table and the filter fields live here rather than in the tool
    description: the description is billed on every request the host makes,
    this is billed only when asked for.
    """
    return {
        "entity": "project_metric",
        "shape": "a time series, not a collection — rows are time buckets",
        "required": ["project_id or project_name", "metric_type"],
        "metric_types": {
            name: {"about": metric.entity, "unit": metric.unit} for name, metric in METRICS.items()
        },
        "intervals": {
            "hourly": "one row per hour",
            "daily": "one row per day",
            "weekly": "one row per week",
            "total": "one row for the whole window",
            "default": (
                "chosen from the window, as the Metrics tab chooses it: hourly up to "
                "3 days, daily up to 30, weekly beyond. Name one to override; a wide "
                "answer (an hourly month is 721 rows) is sent as asked and the header "
                "says which interval applied"
            ),
        },
        "window": (
            "since/until, same forms as every other list; defaults to the last "
            f"{DEFAULT_WINDOW_DAYS} days"
        ),
        "filters": (
            "OQL, same language as list('trace'). The fields are those of the entity the "
            "metric is about (see `about` above). Trace and span metrics default to "
            'source = "sdk" like the other lists; a thread metric takes no source or '
            "environment filter at all — the endpoint's thread field set has neither, "
            "and it drops what it cannot apply instead of saying so."
        ),
        "multi_series": {
            "which": {
                "duration": f"one series per percentile ({', '.join(DURATION_PERCENTILES)})",
                "feedback_scores": "one series per score name",
                "token_usage": "one series per usage key",
            },
            "ungrouped": "every series is returned, one column each",
            "grouped": (
                "the backend charts one of them at a time, so series=<name> picks it: "
                "a percentile, a score name, or a usage key. Defaults where a default "
                f"is honest ({', '.join(f'{k}={v}' for k, v in DEFAULT_SERIES.items())}), "
                "and the chosen one is echoed in the header. A feedback-score metric "
                "has no default — the names are the project's own, and read('project') "
                "lists them."
            ),
        },
        "breakdowns": {
            "syntax": ("breakdown='<field>', or 'metadata.<key>' to group by a metadata key"),
            "by_metric": {name: groupable_by(name) or None for name in METRICS},
            "backend_group_cap": BACKEND_SERIES_CAP,
            "note": (
                "seven metrics accept no grouping at all — they are missing from the "
                "backend's own compatibility sets (BreakdownField), which is a backend "
                "bug rather than a rule: asking anyway returns a message claiming the "
                "field 'supports Span metrics only' about a span metric. Refused "
                "locally instead. A grouping is capped by the backend at "
                f"{BACKEND_SERIES_CAP} groups plus '{OTHERS}', which carries every "
                "group past them (summed per bucket for counts, costs and tokens; "
                "left blank where a sum would not be that metric). Grouped series "
                "are not filled, so a group appears only in the buckets it occurred "
                "in. A metric that fans out per score name or usage key returns every "
                "series it has, one column each; nothing is dropped here."
            ),
        },
        "not_supported": {
            "page, size": "rows are time buckets, not records",
            "sort": "rows are ordered by time",
        },
    }


__all__ = ["reference"]

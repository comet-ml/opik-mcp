"""Dashboards and charts (OPIK-8210).

Three pieces, one vocabulary:

- :mod:`~opik_mcp.charts.vocabulary` — what a chart can be about (metrics,
  breakdowns, intervals, stat names), transcribed from opik-backend's own
  validation rules so a bad combination fails locally with a usable sentence.
- :mod:`~opik_mcp.charts.spec` — ``ChartSpec``, the agent-facing description
  of one chart, compiled either into the widget JSON Opik stores or into the
  metric query that returns its data.
- :mod:`~opik_mcp.charts.config` / :mod:`~opik_mcp.charts.query` — the two
  compilations: a dashboard's stored ``config`` blob, and the ``chart_data``
  tool that runs a chart and summarises the result.

The MCP surface on top is the ``dashboard.*`` operations on the ``write``
tool, the ``dashboard`` entity on ``read``/``list``, and the ``chart_data``
tool.
"""

from opik_mcp.charts.config import (
    DASHBOARD_VERSION,
    DashboardConfigError,
    add_chart,
    build_config,
    flatten_charts,
    remove_widget,
)
from opik_mcp.charts.query import run_chart_data
from opik_mcp.charts.spec import ChartSpec
from opik_mcp.charts.vocabulary import ChartVocabularyError

__all__ = [
    "DASHBOARD_VERSION",
    "ChartSpec",
    "ChartVocabularyError",
    "DashboardConfigError",
    "add_chart",
    "build_config",
    "flatten_charts",
    "remove_widget",
    "run_chart_data",
]

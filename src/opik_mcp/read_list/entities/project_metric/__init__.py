"""``project_metric`` — one metric over time, as a table of buckets.

A time series is not a collection, so almost none of the machinery the other
entities share applies to it: rows are buckets, ``page``/``size``/``sort``
mean nothing, and the filter fields belong to whichever entity the metric is
about. The list tool hands this entity off whole to ``runner.run_project_metric``, and the
registry entry exists so the type is listable and reachable.

- ``catalog`` is what can be asked for: the metrics, the groupings, the
  windows, and every refusal that can be made without calling anything.
- ``runner`` is the order of operations, and the calls.
- ``table`` is what comes back, rendered.
- ``reference`` is what ``schema()`` answers, which only a caller who
  charts something ever pays for.
"""

from __future__ import annotations

from opik_mcp.read_list.entities.project_metric.reference import reference
from opik_mcp.read_list.entities.project_metric.runner import run_project_metric
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.unsupported import unsupported_fetch

HANDLER = EntityHandler(
    entity_type="project_metric",
    fetch_fn=unsupported_fetch,
    run_fn=run_project_metric,
    reference_fn=reference,
    # Deliberately declares no kwargs. The list tool returns before its
    # forwarding gate for this entity, so anything declared here would be
    # dead — and it was also already wrong (no `breakdown`, and
    # `project_name` is accepted). The runner validates its own arguments;
    # a second, unread copy of that contract is worse than none.
    description=(
        "One project metric over time, as a table of buckets. "
        "Reference: schema('list.project_metric')."
    ),
)

__all__ = ["HANDLER"]

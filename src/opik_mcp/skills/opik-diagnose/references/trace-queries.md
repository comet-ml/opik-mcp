---
last_updated: "2026-09-09"
source_commit: "2.0.0"
---

# Trace queries per signal

## MCP

  ```
  list(entity_type="trace", project_name="<project>", since="24h",
       filters="error_info is_not_empty", sort="start_time desc")         # errored
  list(entity_type="span",  project_name="<project>", since="24h",
       filters='type = "tool" AND error_info is_not_empty', sort="start_time desc")  # failed tool calls
  list(entity_type="trace", project_name="<project>", since="24h",
       sort="duration desc")                                              # latency outliers (ms)
  list(entity_type="trace", project_name="<project>", since="24h",
       filters="feedback_scores.<metric> < 0.5", sort="feedback_scores.<metric> asc")  # low online-eval score
  list(entity_type="trace", project_name="<project>", since="7d", until="24h",
       sort="duration desc")                                              # prior window, for regressions
  ```

  The table carries `duration`, `error_type` and cost by default plus every field you sorted or filtered on. A rejected filter comes back with what fixes it; `schema("list.trace")` is the full field reference.

## SDK

  ```python
  traces = client.search_traces(project_name="<project>", max_results=200)  # recent window
  # Narrow server-side with filter_string='error_info is_not_empty' when the volume is
  # large; otherwise rank client-side (SKILL.md step 5, Rank the remaining traces by signal). Each trace carries the fields you rank
  # on: error info, duration, feedback_scores.
  ```

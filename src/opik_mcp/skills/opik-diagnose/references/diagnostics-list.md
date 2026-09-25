---
last_updated: "2026-09-09"
source_commit: "2.0.0"
---

# Reading the Diagnostics list

## Issue to shortlist item

Each open issue becomes one shortlist item with `signal=diagnostics`: `trace_id` comes from the first `example_trace_ids` entry (read the top few issues to get them) and `trace_url` from `trace_url_template` with that id filled in; mention the issue's `url` so the user can open the Diagnostics page itself. `why` carries the issue name, severity, `latest_count` and the cause. The link fields are absent when the server cannot name the UI base or workspace — fall back to the trace link template from the session instructions then (step 6). Keep the list's order — it is the Diagnostics page's ranking (most recently seen first, then most occurrences). Counts are all-time to match the UI; pass `since` (e.g. `"7d"`) to narrow to the window.

## Empty list

**An empty list is not an all-clear.** It says which case it is, and what to do about it. Act on the sentence you get:

| The list says | Do this |
| --- | --- |
| no open issues, last scan `<time>` | Diagnostics is working and found nothing. Continue to step 3. |
| enabled but has not scanned yet, or last scan older than a day | `write('agent_insights_job.trigger', {"project_name": "<project>"})`, report the Diagnostics page link, continue to step 3. No need to ask: a scan changes no data. |
| not enabled for this project, or turned off | Ask the user once, in one sentence: "Enable daily Diagnostics for `<project>`? First results take a few minutes." On yes: `enable`, then `trigger`, report the link, continue. On no: continue and say the shortlist was built without Diagnostics. |
| not available on this deployment | Continue, and say once that this deployment has no Diagnostics. Do not offer to enable it. |

## Coverage line

**A non-empty list is not the whole answer either.** It ends with what the
report covers, `Report covers data through <time>`, because the issues are
whatever the last scan grouped. With a daily scan, today is usually not in
them. When the line goes on to name an uncovered tail, the window the user
asked about runs past the report and the gap is missing from the answer:

| The coverage line says | Do this |
| --- | --- |
| `Report covers data through <time>` and nothing else | The report is current. Continue to step 3 as usual. |
| `The last <N> are not in it: write('agent_insights_job.trigger', …)` | Trigger it, then go to step 3 for the gap with the `since` the line names. Do not wait for the rescan, and do not present the issues as covering the window. Report `source=diagnostics_pending`. |
| `… a trigger rescans the last 24 hours, so it cannot close this gap` | Skip the trigger and go straight to step 3 with the `since` the line names — a rescan would leave the middle of the gap missing while looking like the fix. |

## Without the MCP

**Without the MCP**, the SDK REST client reads the same issues:

```python
import opik

client = opik.Opik()
# Needs the project_id (a uuid), not the name — read it off any trace from
# search_traces (trace.project_id), or resolve it from the project name first.
issues = client.rest_client.agent_insights.find_agent_insights_issues(project_id=project_id)
```

"""``InitializeResult.instructions`` content (ADR 0004 D6).

This blob is delivered once per MCP session on the ``initialize`` handshake
and injected as system-prompt-like context by supported hosts (Claude Code,
Cursor, VS Code, Goose). Hosts that ignore the field lose nothing — each
tool's description is self-contained, so this is purely additive.

The content sets cross-cutting context (workspace, Opik URL, today's date)
and primes the LLM on tool selection. Per GitHub MCP's published data, dynamic
per-session instructions are worth +25pp workflow adherence on capable models
and +60pp on smaller ones, so this is the highest-leverage single dial we
have on tool selection quality.

That leverage cuts both ways: the blob is delivered as authoritative context,
not as a list to verify, so anything it describes had better exist on THIS
connection. It must therefore stay in step with what ``tools/list`` actually
advertises.

The blob is rendered per session (see ``server.install_session_instructions``):
``workspace`` prefers the OAuth-authorized workspace for THIS session — the
inbound ``Comet-Workspace`` header, else the name introspected from the bearer
(``resolved_workspace_name``) — and only falls back to the static ``Settings``
workspace for stdio / API-key installs. ``opik_url`` is the Opik **UI** base,
derived from ``Settings`` (the REST ``OPIK_URL`` minus its ``/api`` suffix).
"""

from __future__ import annotations

from datetime import UTC, datetime

from opik_mcp.config import Settings, get_settings
from opik_mcp.read_list.ui_links import (
    current_workspace,
    opik_ui_base,
    trace_link_template,
)
from opik_mcp.skills_catalog import skill_names
from opik_mcp.writes.registry import WRITE_OPERATIONS

_TEMPLATE = """\
You're connected to Opik (Comet's LLM observability platform){user_clause} \
in workspace "{workspace}". The Opik UI is at {opik_url}.
{default_project_clause}
Tool selection:
- read / list: use for any "show me X" or "what is Y" — these are the cheapest \
reads. read takes (entity_type, id_or_name_or_uri); list takes (entity_type, \
page, size, and a name substring for workspace-wide types). For trace, span, \
thread and experiment, list \
also takes filters (OQL, the same language as search_traces(filter_string=…)), \
sort, since/until and search, so one call answers most questions: \
list('trace', project_name=…, since="1h", filters="error_info is_not_empty", \
sort="duration desc"). Field reference: schema("list.trace"). Readable entity \
types include trace, span, project, experiment, prompt, test_suite, thread, \
agent_insights_issue. Composite reads (trace, prompt, thread, \
agent_insights_issue) inline their child collections so one call usually gets \
the full picture. For a thread, pass the thread link/URI or a project_id — \
read('thread', …) returns the messages list, and list('thread', project_id=…) \
enumerates a project's threads. For "how is my project doing", start with \
read('project', name_or_id): traces, error rate, average duration and cost \
for a window against the window before it, plus the score names and usage \
keys to filter on, and the freshest experiment, suite, prompt version and \
run. since/until move the summary's window; the UI opens on since="30d". \
To attribute a change rather than just report it, chart it: \
list('project_metric', project_name=…, metric_type="span_count", \
breakdown="model"); reference schema("list.project_metric"), which says \
which metrics take which grouping. For "what is \
broken in production", start with \
list('agent_insights_issue', project_name=…): the project's Diagnostics (Agent \
Insights) issues — recurring failures already grouped and ranked, open ones by \
default, counts all-time unless since/until narrow them — instead of ranking \
raw traces yourself; read('agent_insights_issue', id, project_name=…) adds the \
cause, the suggested fix, example_trace_ids to open with read('trace', …), and \
UI links. An empty issue list says why it is empty — unavailable on this \
deployment, never enabled for the project, turned off, enabled but not \
scanned recently, or enabled and clean — and where it can be fixed, \
write('agent_insights_job.enable', …) turns Diagnostics on for \
the project (ask the user first: it creates a standing daily scan) and \
write('agent_insights_job.trigger', …) scans now instead of waiting for the \
nightly run. A non-empty list dates itself ("Report covers data through …") \
because the issues are whatever the last scan grouped; when it names an \
uncovered tail, the requested window runs past the report, so close the gap \
with list('trace', …, since=…) instead of answering from the issues \
alone.{trace_link_clause}
- Direct writes — use when the user's intent is concrete and well-defined \
("score this trace 0.8 on helpfulness", "comment 'retry with temperature=0' \
on span X"). The full write surface is two tools: write (takes \
operation + data; pass a list for batch) and schema (returns an op's JSON \
Schema + bundled example). Operations covered by Phase 1: \
{write_operations}. Always consult \
tools/list for what's actually advertised on this connection.
- read_skill: Opik's own agent skills ({skill_names}) ship with this server. \
Load the relevant one BEFORE instrumenting, evaluating, or debugging an Opik \
task — unless it's already in your context, in which case use what you have.

Today's date is {date}.\
"""


def _opik_ui_url(s: Settings) -> str:
    """Opik **UI** base URL for the blob, or a generic placeholder if unconfigured.

    Shares :func:`opik_ui_base` with the links a ``read`` attaches, so the blob
    and the per-entity links can never disagree about where the UI lives.
    """
    base = opik_ui_base(s)
    return base if base is not None else "(Opik URL not configured)"


def _render_trace_link_clause(s: Settings) -> str:
    """Name the trace link shape once per session, or say nothing.

    A trace id is not something a user can act on, and neither ``list`` nor
    ``read`` returns a URL for one. The shape is not guessable — it goes
    through the backend redirect with a base64 argument — and a guess yields a
    link that looks right and 404s, which is worse than the bare id. Naming the
    template on the handshake costs nothing per call and needs no project_id.
    """
    template = trace_link_template(s)
    if template is None:
        return ""
    return (
        " A trace id is not clickable: hand the user a link instead, "
        f"{template}, with the id filled in."
    )


def _render_default_project_clause(s: Settings) -> str:
    pname = s.opik_default_project_name
    if not pname:
        return ""
    return (
        f'\nThe user\'s default project is `project_name="{pname}"`. Pass it '
        "as `project_name` to any tool/operation that accepts one (write "
        "operations like score.create / trace.create) unless the user "
        "explicitly names a different project.\n"
    )


def render_instructions(
    settings: Settings | None = None,
    *,
    user_email: str | None = None,
    today: datetime | None = None,
) -> str:
    """Render the instructions blob for the current session.

    ``user_email`` is omitted from the rendered text when unknown — better
    no claim than a stale claim. Same for ``opik_url`` — falls back to a
    generic placeholder if the config is partial.

    Workspace precedence (most to least authoritative for THIS session): an
    explicit inbound ``Comet-Workspace`` header → the OAuth-introspected
    ``resolved_workspace_name`` → the static ``Settings`` workspace → ``"default"``.
    """
    s = settings if settings is not None else get_settings()
    workspace = current_workspace(s)
    opik_url = _opik_ui_url(s)

    user_clause = f" as {user_email}" if user_email else ""
    today = today if today is not None else datetime.now(UTC)
    date = today.strftime("%Y-%m-%d")
    default_project_clause = _render_default_project_clause(s)

    return _TEMPLATE.format(
        user_clause=user_clause,
        workspace=workspace,
        opik_url=opik_url,
        date=date,
        default_project_clause=default_project_clause,
        trace_link_clause=_render_trace_link_clause(s),
        write_operations=", ".join(sorted(WRITE_OPERATIONS)),
        skill_names=", ".join(skill_names()),
    )


__all__ = ["render_instructions"]

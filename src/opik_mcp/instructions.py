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
advertises — ``ask_ollie`` is opt-in and removed from the registry when off, so
its passages render only when it is enabled (``_render_ask_ollie_clauses``).

The blob is rendered per session (see ``server.install_session_instructions``):
``workspace`` prefers the OAuth-authorized workspace for THIS session — the
inbound ``Comet-Workspace`` header, else the name introspected from the bearer
(``resolved_workspace_name``) — and only falls back to the static ``Settings``
workspace for stdio / API-key installs. ``opik_url`` is the Opik **UI** base,
derived from ``Settings`` (the REST ``OPIK_URL`` minus its ``/api`` suffix).
"""

from __future__ import annotations

from datetime import UTC, datetime

from opik_mcp.auth_context import inbound_workspace, resolved_workspace_name
from opik_mcp.config import DEFAULT_WORKSPACE, Settings, get_settings
from opik_mcp.opik_client import opik_rest_base
from opik_mcp.skills_catalog import skill_names
from opik_mcp.writes.registry import WRITE_OPERATIONS

_TEMPLATE = """\
You're connected to Opik (Comet's LLM observability platform){user_clause} \
in workspace "{workspace}". The Opik UI is at {opik_url}.
{default_project_clause}
Tool selection:
- read / list: use for any "show me X" or "what is Y" — these are the cheapest \
reads. read takes (entity_type, id_or_name_or_uri); list takes (entity_type, \
optional name filter, page, size). Readable entity types include trace, span, \
project, experiment, prompt, test_suite, thread. Composite reads (trace, prompt, \
thread) inline their child collections so one call usually gets the full picture. \
For a thread, pass the thread link/URI or a project_id — read('thread', …) \
returns the messages list, and list('thread', project_id=…) enumerates a \
project's threads.
- Direct writes — use when the user's intent is concrete and well-defined \
("score this trace 0.8 on helpfulness", "comment 'retry with temperature=0' \
on span X").{prefer_narrow_clause} The full write surface is two tools: write (takes \
operation + data; pass a list for batch) and schema (returns an op's JSON \
Schema + bundled example). Operations covered by Phase 1: \
{write_operations}. run_experiment is a separate tool. Always consult \
tools/list for what's actually advertised on this connection.{ask_ollie_clause}
- read_skill: Opik's own agent skills ({skill_names}) ship with this server. \
Load the relevant one BEFORE instrumenting, evaluating, or debugging an Opik \
task — unless it's already in your context, in which case use what you have.

Today's date is {date}.\
"""


def _opik_ui_url(s: Settings) -> str:
    """Opik **UI** base URL for the blob, or a generic placeholder if unconfigured.

    Derived from :func:`opik_rest_base` — the single source of truth for where
    Opik lives (``OPIK_URL`` override, else ``COMET_URL_OVERRIDE + "/opik/api"``)
    — so the UI link can never drift from where REST calls actually go. That base
    is the REST **API** base (``…/opik/api``); the UI lives at the same origin
    without the trailing ``/api`` segment, so we strip it.
    """
    base = opik_rest_base(s)
    if base is None:
        return "(Opik URL not configured)"
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base


def _render_default_project_clause(s: Settings) -> str:
    pname = s.opik_default_project_name
    if not pname:
        return ""
    # ``ask_ollie`` is only worth naming as an example when this deployment
    # actually advertises it (see ``_render_ask_ollie_clauses``).
    examples = (
        "ask_ollie, and write operations like score.create / trace.create"
        if s.opik_mcp_ask_ollie_enabled
        else "write operations like score.create / trace.create"
    )
    return (
        f'\nThe user\'s default project is `project_name="{pname}"`. Pass it '
        f"as `project_name` to any tool/operation that accepts one ({examples}) "
        "unless the user explicitly names a different project.\n"
    )


def _render_ask_ollie_clauses(s: Settings) -> tuple[str, str]:
    """The two ask_ollie passages, or empty strings when it isn't advertised.

    ``ask_ollie`` is opt-in (``OPIK_MCP_ASK_OLLIE_ENABLED``, default false) and
    ``server.apply_tool_visibility`` *removes* it from the registry when it is
    off — the tool never reaches ``tools/list``. This blob was still describing
    it in every session regardless, which is the exact failure that removal
    exists to prevent: an agent spending a turn discovering that a tool it was
    told to use does not exist. Worse than a missing tool, because the blob is
    delivered as authoritative context rather than as a list to check.

    Returns ``(prefer_narrow_clause, ask_ollie_clause)`` — the aside steering
    concrete writes away from Ollie, and the bullet describing it.
    """
    if not s.opik_mcp_ask_ollie_enabled:
        return "", ""
    prefer_narrow = " Skip ask_ollie for these — narrower tools are faster and more deterministic."
    bullet = (
        '\n- ask_ollie: use for investigative questions ("why is X failing?"), '
        'cross-entity synthesis ("compare experiments A and B"), or when '
        "authoring / instrumentation requires Opik domain expertise. Returns a "
        "thread_id you can pass back for follow-ups. Writes Ollie performs "
        "mid-stream (scores, comments, test-suite items, prompts) execute "
        "without a per-action confirmation step — be intentional about what you "
        "ask for."
    )
    return prefer_narrow, bullet


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
    workspace = (
        inbound_workspace.get()
        or resolved_workspace_name.get()
        or s.comet_workspace
        or DEFAULT_WORKSPACE
    )
    opik_url = _opik_ui_url(s)

    user_clause = f" as {user_email}" if user_email else ""
    today = today if today is not None else datetime.now(UTC)
    date = today.strftime("%Y-%m-%d")
    default_project_clause = _render_default_project_clause(s)
    prefer_narrow_clause, ask_ollie_clause = _render_ask_ollie_clauses(s)

    return _TEMPLATE.format(
        user_clause=user_clause,
        workspace=workspace,
        opik_url=opik_url,
        date=date,
        default_project_clause=default_project_clause,
        prefer_narrow_clause=prefer_narrow_clause,
        ask_ollie_clause=ask_ollie_clause,
        write_operations=", ".join(sorted(WRITE_OPERATIONS)),
        skill_names=", ".join(skill_names()),
    )


__all__ = ["render_instructions"]

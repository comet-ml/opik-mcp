"""``InitializeResult.instructions`` content (ADR 0004 D6).

This blob is delivered once per MCP session on the ``initialize`` handshake
and injected as system-prompt-like context by supported hosts (Claude Code,
Cursor, VS Code, Goose). Hosts that ignore the field lose nothing — each
tool's description is self-contained, so this is purely additive.

The content sets cross-cutting context (workspace, Opik URL, today's date)
and primes the LLM on when to prefer ``read``/``list``/direct writes vs.
``ask_ollie``. Per GitHub MCP's published data, dynamic per-session
instructions are worth +25pp workflow adherence on capable models and
+60pp on smaller ones, so this is the highest-leverage single dial we
have on tool selection quality.

Phase 1 ships static-template behavior — user_email and full per-session
context will land when the OAuth/identity path is wired up (Phase 2). For
now, ``workspace`` and ``opik_url`` come from ``Settings``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from opik_mcp.config import DEFAULT_WORKSPACE, Settings, get_settings
from opik_mcp.writes.registry import WRITE_OPERATIONS

_TEMPLATE = """\
You're connected to Opik (Comet's LLM observability platform){user_clause} \
in workspace "{workspace}". The Opik UI is at {opik_url}.
{default_project_clause}
Tool selection:
- read / list: use for any "show me X" or "what is Y" — these are the cheapest \
reads. read takes (entity_type, id_or_name_or_uri); list takes (entity_type, \
optional name filter, page, size). Readable entity types include trace, span, \
project, experiment, prompt, test_suite. Composite reads (trace, prompt) inline \
their child collections so one call usually gets the full picture.
- Direct writes — use when the user's intent is concrete and well-defined \
("score this trace 0.8 on helpfulness", "comment 'retry with temperature=0' \
on span X"). Skip ask_ollie for these — narrower tools are faster and more \
deterministic. The full write surface is two tools: write (takes \
operation + data; pass a list for batch) and schema (returns an op's JSON \
Schema + bundled example). Operations covered by Phase 1: \
{write_operations}. run_experiment is a separate tool. Always consult \
tools/list for what's actually advertised on this connection.
- ask_ollie: use for investigative questions ("why is X failing?"), cross-entity \
synthesis ("compare experiments A and B"), or when authoring / instrumentation \
requires Opik domain expertise. Returns a thread_id you can pass back for \
follow-ups. Writes Ollie performs mid-stream (scores, comments, test-suite \
items, prompts) execute without a per-action confirmation step — be \
intentional about what you ask for.

Today's date is {date}.\
"""


def _render_default_project_clause(s: Settings) -> str:
    pname = s.opik_default_project_name
    if not pname:
        return ""
    return (
        f'\nThe user\'s default project is `project_name="{pname}"`. Pass it '
        "as `project_name` to any tool/operation that accepts one (ask_ollie, "
        "and write operations like score.create / trace.create) unless the "
        "user explicitly names a different project.\n"
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
    """
    s = settings if settings is not None else get_settings()
    workspace = s.comet_workspace or DEFAULT_WORKSPACE
    if s.opik_url:
        opik_url = s.opik_url.rstrip("/")
    elif s.comet_url_override:
        opik_url = f"{s.comet_url_override.rstrip('/')}/opik"
    else:
        opik_url = "(Opik URL not configured)"

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
        write_operations=", ".join(sorted(WRITE_OPERATIONS)),
    )


__all__ = ["render_instructions"]

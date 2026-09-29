"""What a cost intelligence server tells the host: tool descriptions, argument
texts and the ``initialize`` instructions. Kept apart from the default text so
the two surfaces cannot drift into each other."""

from __future__ import annotations

from typing import Final

from opik_mcp.cost_intelligence import FIXED_PROJECT

GUIDE_NAME: Final = "cost-intelligence"
GUIDE_FILE: Final = "cost-intelligence.md"
# The bundled skills this mode offers besides the guide.
OFFERED_BUNDLED_SKILLS: Final = ("opik",)
OFFERED_SKILLS: Final = tuple(sorted((GUIDE_NAME, *OFFERED_BUNDLED_SKILLS)))

READ_DESCRIPTION: Final = (
    "Read one record whole, by id or pasted Opik link: trace (with its spans), span, "
    "thread (its messages), project (a window summary). Output is a `[read: …]` size "
    "header, then compact JSON. `fields` returns only the paths named; a large record "
    "is narrowed with `fields` or by reading a span rather than its trace."
)
LIST_DESCRIPTION: Final = (
    "List records as a pipe-delimited table under a size header, with the call for the "
    "next page. trace, span and thread take filters, sort, since/until and search; "
    "project_metric charts one metric over time (metric_type). Fields of each: "
    "schema('list.<type>')."
)
SCHEMA_DESCRIPTION: Final = (
    "Return the filterable and sortable fields of a list call, for `list.<type>`. "
    "Pure lookup, no backend call."
)
SCHEMA_TITLE: Final = "Show a list call's fields"
READ_SKILL_DESCRIPTION: Final = (
    "Load an agent guide. `cost-intelligence`: what is logged and how to read a "
    "session. `opik`: SDK lookups. Use it when the guide is not "
    "already in your context. `skill_name` is a name or `opik/references/<file>.md`."
)

# Replacements for argument texts that name an entity or skill this mode hides.
ARGUMENT_TEXT: Final[dict[tuple[str, str], str]] = {
    ("read", "id"): (
        "UUID, full opik:// URI or a pasted Opik link. A URI or link overrides entity_type."
    ),
    ("read", "since"): (
        "Start of the window, as a relative span ('7d', '24h') or an ISO-8601 instant "
        "with timezone. Moves a project summary's period (7d by default)."
    ),
    ("list", "name"): "Optional substring filter on the project name.",
    ("list", "filters"): (
        "OQL filter for trace, span, thread, project_metric: <field>[.<key>] <op> <value> "
        "[AND ...]; ops = != > >= < <= contains not_contains starts_with ends_with "
        "is_empty is_not_empty in not_in; strings quoted, numbers bare (duration in ms). "
        "E.g. 'error_info is_not_empty AND duration > 5000'. trace/span/thread default "
        'to source = "sdk". Reference: schema("list.trace").'
    ),
    ("list", "sort"): (
        "Sort for trace, span, thread: '<field> [asc|desc]', desc by default. E.g. "
        "'duration desc', 'total_estimated_cost', 'usage.total_tokens'. One field only."
    ),
    ("list", "since"): (
        "Start of the time window for trace, span, thread, project_metric: a relative "
        "span ('30m', '1h', '24h', '7d') or an ISO-8601 instant with timezone."
    ),
    ("schema", "operation"): "list.<type> for the filter and sort reference of that list call.",
    ("read_skill", "skill_name"): (
        "A guide name ('cost-intelligence') or a path inside one ('opik/references/<file>.md')."
    ),
}

_INSTRUCTIONS_TEMPLATE = """\
You're connected to Opik cost intelligence{user_clause} in workspace "{workspace}": \
one organization's Claude Code usage. Every call is scoped to the `{project}` \
project. The Opik UI is at {opik_url}.

Opik types answer what happened: a thread is a session, a trace is a turn (its name \
starts with the user's request; skip "automated…" turns), tool spans are named \
`tool_use: <Tool>`. Opik's own cost fields read $0 here; do not report them. \
schema('list.<type>') gives a list call's fields. "I" and "me" mean {caller}; "we" \
and "our org" mean no user filter.

read_skill('{guide}') has what is logged and how to outline a session; read it before \
the first answer.

Links: a read carries the `url` of what it returned, a list page a `url_template` or \
a `url` per row. Put the link on the name of every row you mention; never guess or \
shorten one.

Today's date is {date}.\
"""


def render_instructions_text(
    *, user_email: str | None, workspace: str, opik_url: str, date: str
) -> str:
    return _INSTRUCTIONS_TEMPLATE.format(
        user_clause=f" as {user_email}" if user_email else "",
        workspace=workspace,
        project=FIXED_PROJECT,
        opik_url=opik_url,
        caller=user_email or "the caller (email unknown)",
        guide=GUIDE_NAME,
        date=date,
    )


__all__ = [
    "ARGUMENT_TEXT",
    "GUIDE_FILE",
    "GUIDE_NAME",
    "LIST_DESCRIPTION",
    "OFFERED_BUNDLED_SKILLS",
    "OFFERED_SKILLS",
    "READ_DESCRIPTION",
    "READ_SKILL_DESCRIPTION",
    "SCHEMA_DESCRIPTION",
    "SCHEMA_TITLE",
    "render_instructions_text",
]

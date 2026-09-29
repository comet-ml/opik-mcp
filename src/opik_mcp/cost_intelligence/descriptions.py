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
    "thread (its messages), project (a window summary), spend_lane (one lane's items), "
    "spend_session (what one session did). Output is a `[read: …]` size header, then "
    "compact JSON. `fields` returns only the paths named; a large record is narrowed "
    "with `fields` or by reading a span rather than its trace."
)
LIST_DESCRIPTION: Final = (
    "List records as a pipe-delimited table under a size header, with the call for the "
    "next page. trace, span and thread take filters, sort, since/until and search; "
    "project_metric charts one metric over time (metric_type). Spend: spend_summary "
    "(against the previous window), spend_lane, spend_user (ranked by total tokens), "
    "spend_session, spend_agent. Fields of each: schema('list.<type>')."
)
SCHEMA_DESCRIPTION: Final = (
    "Return the filterable and sortable fields of a list call, for `list.<type>`. "
    "Pure lookup, no backend call."
)
SCHEMA_TITLE: Final = "Show a list call's fields"
READ_SKILL_DESCRIPTION: Final = (
    "Load an agent guide. `cost-intelligence`: the spend lanes, what is logged and "
    "recipes for spend questions. `opik`: SDK lookups. Use it when the guide is not "
    "already in your context. `skill_name` is a name or `opik/references/<file>.md`."
)

# Replacements for argument texts that name an entity or skill this mode hides.
ARGUMENT_TEXT: Final[dict[tuple[str, str], str]] = {
    ("read", "id"): (
        "UUID, full opik:// URI or a pasted Opik link; a lane name for spend_lane, a "
        "session id for spend_session. A URI or link overrides entity_type."
    ),
    ("read", "since"): (
        "Start of the window, as a relative span ('7d', '24h') or an ISO-8601 instant "
        "with timezone. Moves a project summary's period (7d by default) and the "
        "spend types' window."
    ),
    ("list", "name"): "Optional substring filter on the project name.",
    ("list", "filters"): (
        "OQL filter for trace, span, thread, project_metric: <field>[.<key>] <op> <value> "
        "[AND ...]; ops = != > >= < <= contains not_contains starts_with ends_with "
        "is_empty is_not_empty in not_in; strings quoted, numbers bare (duration in ms). "
        "E.g. 'error_info is_not_empty AND duration > 5000'. trace/span/thread default "
        'to source = "sdk". The spend types take their own: user_email = "…", '
        'mcp_server = "…". Reference: schema("list.trace").'
    ),
    ("list", "sort"): (
        "Sort for trace, span, thread: '<field> [asc|desc]', desc by default. E.g. "
        "'duration desc', 'total_estimated_cost', 'usage.total_tokens'. One field only."
    ),
    ("list", "since"): (
        "Start of the time window for trace, span, thread, project_metric and the spend "
        "types: a relative span ('30m', '1h', '24h', '7d') or an ISO-8601 instant with "
        "timezone. Spend types default to 30 days."
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

Money questions go to the spend types first:
- list('spend_summary'): spend and tokens against the previous window.
- list('spend_lane'), then read('spend_lane', '<lane>'): what the spend is made of.
- list('spend_user'): who spends, ranked by total tokens. \
filters='mcp_server = "x"' (or skill, built_in_tool) lists who uses one item.
- list('spend_session'), then read('spend_session', '<id>'): sessions by tokens, \
then what one session did.
- list('spend_agent'): spend by agent.
since/until set the window (default 30 days). filters='user_email = "…"' narrows to \
one person: "I" and "me" mean {caller}; "we" and "our org" mean no filter.

Dollars come only from the spend types; say "billed" or "list price". Opik's own cost \
fields read $0 here. Say "by tokens" when you rank. The spend types need an org \
admin's API key; say so when a call is refused for it.

Opik types answer what happened: a thread is a session (same id as spend_session), a \
trace is a turn (its name starts with the user's request; skip "automated…" turns), \
tool spans are named `tool_use: <Tool>`. schema('list.<type>') gives a list call's \
fields.

read_skill('{guide}') has the lanes, what is logged and recipes; read it before the \
first spend answer.

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

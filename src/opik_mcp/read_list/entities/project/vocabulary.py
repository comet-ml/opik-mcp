"""A project's vocabulary: the names an agent needs before it can ask anything.

The summary says how a project is doing. This says what can be asked about it
— which feedback scores exist, which usage keys were recorded, what is scoring
the traces. Each was its own discovery call, and an agent that did not know to
make them wrote filters against guessed names and read the empty result as
good news.

This is a map, not content. Score names and rules are capped by count, state
the true total, and name the call that pages through all of them. Usage keys
are not capped: no call enumerates them on their own, so a cut there had no
way back — and they are short strings the backend sends in full anyway.

Those two lists are read through ``project_names`` rather than fetched here:
a metric series checks its ``series`` against the same names, and a fact two
entities need belongs to neither.

An empty part is omitted rather than returned empty: "nothing recorded yet"
and "could not load" have to stay distinguishable, and a failed part carries
an ``error`` instead of names for the same reason the summary does.
"""

from __future__ import annotations

from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.decorations import block
from opik_mcp.read_list.project_names import (
    SCORE_NAMES_CAP,
    fetch_score_names,
    fetch_usage_keys,
    named,
)

RULES_CAP: Final = 10
"""The page size asked of the evaluators endpoint — ours, not a backend limit.
The endpoint has no maximum; ten is enough to say what is scoring the project,
and ``list('online_rule')`` pages through the rest."""


def _part(names: list[str]) -> dict[str, Any] | None:
    """One whole vocabulary part, or ``None`` when there is nothing to say.

    ``total`` is reported here too, not only on a capped part: a total that
    appeared only when something was cut would make its mere presence mean
    "truncated", and the agent would have to infer completeness.
    """
    return {"names": names, "total": len(names)} if names else None


def _capped_part(
    names: list[str], *, cap: int, all_of_them: str, total: int | None = None
) -> dict[str, Any] | None:
    """A part cut to ``cap`` names, with the call that returns all of them.

    The pointer is not optional: a cap with nowhere to send the caller is a cut
    with no way back, which is why usage keys go through :func:`_part` instead.
    """
    if not names:
        return None
    counted = total if total is not None else len(names)
    part: dict[str, Any] = {"names": names[:cap], "total": counted}
    # With no total to compare against, a list that fills the cap may or may
    # not be all of them; the pointer goes on rather than risk a cut that
    # says nothing. A list under the cap is complete either way.
    if counted > cap or (total is None and len(names) >= cap):
        part["all"] = all_of_them
    return part


async def score_names(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    async def load() -> dict[str, Any] | None:
        return _capped_part(
            await fetch_score_names(client, project_id),
            cap=SCORE_NAMES_CAP,
            all_of_them=f"list('score_name', project_id='{project_id}')",
        )

    return await block("this project's score names", load)


async def usage_keys(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    async def load() -> dict[str, Any] | None:
        return _part(await fetch_usage_keys(client, project_id))

    return await block("this project's usage keys", load)


async def online_rules(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    """Rule names only — the kinds and sampling rates are one list call away,
    and the overview's job is to say that rules exist and what they are called."""

    async def load() -> dict[str, Any] | None:
        body = await client.list_automation_rules(project_id=project_id, size=RULES_CAP)
        total_raw = body.get("total")
        return _capped_part(
            named(body.get("content")),
            cap=RULES_CAP,
            total=total_raw if isinstance(total_raw, int) and total_raw >= 0 else None,
            all_of_them=f"list('online_rule', project_id='{project_id}')",
        )

    return await block("this project's online rules", load)


def assemble(
    scores: dict[str, Any] | None,
    usage: dict[str, Any] | None,
    rules: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The vocabulary block, or ``None`` when the project has no vocabulary.

    A project that has never been scored, never reported usage and has no rules
    gets no block at all rather than three empty ones — the absence is the
    answer.
    """
    block = {
        name: part
        for name, part in (
            ("score_names", scores),
            ("usage_keys", usage),
            ("online_rules", rules),
        )
        if part is not None
    }
    return block or None


__all__ = [
    "RULES_CAP",
    "assemble",
    "online_rules",
    "score_names",
    "usage_keys",
]

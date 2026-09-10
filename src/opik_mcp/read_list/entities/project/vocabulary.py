"""A project's vocabulary: the names an agent needs before it can ask anything.

The summary says how a project is doing. This says what can be asked about it
— which feedback scores exist, which usage keys were recorded, what is scoring
the traces. Each was its own discovery call, and an agent that did not know to
make them wrote filters against guessed names and read the empty result as
good news.

This is a map, not content. Two of the three lists are unbounded on the
backend — the score-name query is a ``distinct name`` with no ``LIMIT``, and
usage keys are whatever the instrumentation reported — so each part is capped
by count and always states the true total. Where a caller can get the rest,
the part says which call returns it; where no such call exists, it says so
rather than implying one.

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
    USAGE_KEYS_CAP,
    fetch_score_names,
    fetch_usage_keys,
    named,
)

RULES_CAP: Final = 10
"""One page of the evaluators endpoint, which is where the cap comes from."""


def _part(
    names: list[str],
    *,
    cap: int,
    total: int | None = None,
    all_of_them: str | None = None,
) -> dict[str, Any] | None:
    """One vocabulary part, capped, or ``None`` when there is nothing to say.

    ``total`` is always reported, not only on truncation: a total that appeared
    only when something was cut would make its mere presence mean "truncated",
    and the agent would have to infer completeness. The pointer is what signals
    truncation.
    """
    if not names:
        return None
    counted = total if total is not None else len(names)
    part: dict[str, Any] = {"names": names[:cap], "total": counted}
    if counted > cap:
        part["all"] = (
            all_of_them
            if all_of_them is not None
            else f"the first {cap} of {counted}; the rest are not enumerable on their own"
        )
    return part


async def score_names(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    async def load() -> dict[str, Any] | None:
        return _part(
            await fetch_score_names(client, project_id),
            cap=SCORE_NAMES_CAP,
            all_of_them=f"list('score_name', project_id='{project_id}')",
        )

    return await block("this project's score names", load)


async def usage_keys(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    async def load() -> dict[str, Any] | None:
        return _part(await fetch_usage_keys(client, project_id), cap=USAGE_KEYS_CAP)

    return await block("this project's usage keys", load)


async def online_rules(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    """Rule names only — the kinds and sampling rates are one list call away,
    and the overview's job is to say that rules exist and what they are called."""

    async def load() -> dict[str, Any] | None:
        body = await client.list_automation_rules(project_id=project_id, size=RULES_CAP)
        total_raw = body.get("total")
        return _part(
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

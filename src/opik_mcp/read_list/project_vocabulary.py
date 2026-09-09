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

An empty part is omitted rather than returned empty: "nothing recorded yet"
and "could not load" have to stay distinguishable, and a failed part carries
an ``error`` instead of names for the same reason the summary does.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikReadClient,
    OpikServerError,
    OpikValidationError,
)

logger = logging.getLogger("opik_mcp.read_list.project_vocabulary")

SCORE_NAMES_CAP: Final = 25
"""Enough to see a project's real vocabulary; a judge rule per metric plus
per-author scores can run to hundreds, which would swallow the read."""

USAGE_KEYS_CAP: Final = 15
"""Standard token keys number about six. The cap is for a project whose
instrumentation invents its own."""

RULES_CAP: Final = 10
"""One page of the evaluators endpoint, which is where the cap comes from."""

_BACKEND_ERRORS: Final = (
    OpikAuthError,
    OpikNotFoundError,
    OpikValidationError,
    OpikServerError,
)


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
    try:
        body = await client.list_project_score_names(project_id)
    except _BACKEND_ERRORS as exc:
        logger.debug("project %s score names failed: %s", project_id, exc)
        return {"error": f"Could not load this project's score names: {exc}"}
    raw = body.get("scores")
    names = (
        [row["name"] for row in raw if isinstance(row, dict) and isinstance(row.get("name"), str)]
        if isinstance(raw, list)
        else []
    )
    return _part(
        names,
        cap=SCORE_NAMES_CAP,
        all_of_them=f"list('score_name', project_id='{project_id}')",
    )


async def usage_keys(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    try:
        body = await client.list_project_token_usage_names(project_id)
    except _BACKEND_ERRORS as exc:
        logger.debug("project %s usage keys failed: %s", project_id, exc)
        return {"error": f"Could not load this project's usage keys: {exc}"}
    raw = body.get("names")
    names = [key for key in raw if isinstance(key, str)] if isinstance(raw, list) else []
    return _part(names, cap=USAGE_KEYS_CAP)


async def online_rules(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    """Rule names only — the kinds and sampling rates are one list call away,
    and the overview's job is to say that rules exist and what they are called."""
    try:
        body = await client.list_automation_rules(project_id=project_id, size=RULES_CAP)
    except _BACKEND_ERRORS as exc:
        logger.debug("project %s automation rules failed: %s", project_id, exc)
        return {"error": f"Could not load this project's online rules: {exc}"}
    raw = body.get("content")
    names = (
        [row["name"] for row in raw if isinstance(row, dict) and isinstance(row.get("name"), str)]
        if isinstance(raw, list)
        else []
    )
    total_raw = body.get("total")
    return _part(
        names,
        cap=RULES_CAP,
        total=total_raw if isinstance(total_raw, int) and total_raw >= 0 else None,
        all_of_them=f"list('online_rule', project_id='{project_id}')",
    )


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
    "SCORE_NAMES_CAP",
    "USAGE_KEYS_CAP",
    "assemble",
    "online_rules",
    "score_names",
    "usage_keys",
]

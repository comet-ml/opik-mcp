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

One part is sampled rather than enumerated. The experiments' metadata keys
have no endpoint of their own; they are read off the freshest page of runs.
Such a part carries ``sampled_from`` beside ``total``, so ``total`` is read
as "distinct keys in the sample" and a key seen once is not taken for a
convention, and it carries the ``filter`` that uses a key. It has no ``all``
pointer for the same reason usage keys have none: no call enumerates the
rest, so there is nothing to point at.
"""

from __future__ import annotations

import json
from collections import Counter
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

METADATA_SAMPLE: Final = 25
"""Experiments read for their metadata keys: the freshest page, no more.

``metadata.<key>`` is the only filter that reaches into how a run was
configured — the model, the optimizer, the prompt — and it needs the key.
Nothing enumerated the keys, so an agent wrote ``metadata.model`` against runs
that had recorded ``agent_config`` and read the empty page as "no run used
that model". The keys are a property of how a project's experiments are
written, not of each one: twenty-five recent runs say what the next filter
can name, and a scan of the rest would say the same thing slower."""


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


async def experiment_metadata_keys(
    client: OpikReadClient, project_id: str
) -> dict[str, Any] | None:
    """The top-level ``metadata`` keys the project's recent experiments carry,
    most common first — the names a ``metadata.<key>`` filter can take.

    Top level only, because that is as deep as the filter reaches: the
    backend's dictionary filter addresses one key. The part says what it was
    sampled from, so a key seen once in twenty-five runs is not mistaken for
    a convention.
    """

    async def load() -> dict[str, Any] | None:
        scope = [{"field": "project_id", "operator": "=", "key": "", "value": project_id}]
        body = await client.list_experiments(
            filters=json.dumps(scope, separators=(",", ":")), size=METADATA_SAMPLE
        )
        rows = body.get("content") if isinstance(body, dict) else None
        rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        seen: Counter[str] = Counter()
        for row in rows:
            metadata = row.get("metadata")
            if isinstance(metadata, dict):
                seen.update(key for key in metadata if isinstance(key, str))
        part = _part([key for key, _ in seen.most_common()])
        if part is None:
            return None
        part["sampled_from"] = len(rows)
        part["filter"] = "list('experiment', filters='metadata.<key> = \"…\"')"
        return part

    return await block("this project's experiment metadata keys", load)


def assemble(
    scores: dict[str, Any] | None,
    usage: dict[str, Any] | None,
    rules: dict[str, Any] | None,
    metadata_keys: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The vocabulary block, or ``None`` when the project has no vocabulary.

    A project that has never been scored, never reported usage, has no rules
    and no experiment metadata gets no block at all rather than four empty
    ones — the absence is the answer.
    """
    block = {
        name: part
        for name, part in (
            ("score_names", scores),
            ("usage_keys", usage),
            ("online_rules", rules),
            ("experiment_metadata_keys", metadata_keys),
        )
        if part is not None
    }
    return block or None


__all__ = [
    "METADATA_SAMPLE",
    "RULES_CAP",
    "assemble",
    "experiment_metadata_keys",
    "online_rules",
    "score_names",
    "usage_keys",
]

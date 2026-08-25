"""Seed a unique Opik project with attention-worthy traces for the /find evals.

Emits: 1 errored, 1 slow, 1 low-online-eval-score, and 2 normal traces into a
fresh project (unique name per run, so precision checks aren't polluted by
earlier runs). Writes planted.json = {project, roles: {error, slow, lowscore,
normal:[...]}} so the grader knows which trace ids SHOULD and SHOULD NOT appear
in the shortlist.
"""

import json
import os
import time
import uuid
from pathlib import Path

PROJECT = os.environ.get("FIND_EVAL_PROJECT") or ("opik-find-eval-" + uuid.uuid4().hex[:8])
os.environ["OPIK_PROJECT_NAME"] = PROJECT

import opik  # noqa: E402
from opik import opik_context  # noqa: E402

roles: dict = {}


@opik.track
def normal(q: str) -> str:
    roles.setdefault("normal", []).append(opik_context.get_current_trace_data().id)
    return "ok: " + q


@opik.track
def slow(q: str) -> str:
    roles["slow"] = opik_context.get_current_trace_data().id
    time.sleep(3.0)
    return "slow ok"


@opik.track
def erroring(q: str) -> str:
    roles["error"] = opik_context.get_current_trace_data().id
    raise ValueError("downstream API returned 500")


@opik.track
def lowscore(q: str) -> str:
    roles["lowscore"] = opik_context.get_current_trace_data().id
    opik_context.update_current_trace(
        feedback_scores=[{"name": "Hallucination", "value": 0.95, "reason": "unsupported claims"}]
    )
    return "confident but wrong"


if __name__ == "__main__":
    normal("a")
    normal("b")
    slow("c")
    lowscore("d")
    try:
        erroring("e")
    except Exception:
        pass
    opik.flush_tracker()
    time.sleep(4)  # let ingestion settle before the skill queries
    out = {"project": PROJECT, "roles": roles}
    Path("planted.json").write_text(json.dumps(out, indent=2))
    print("PLANTED", json.dumps(out))

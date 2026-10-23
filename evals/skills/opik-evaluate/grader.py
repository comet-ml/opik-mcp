# mypy: ignore-errors
"""Deterministic grader for the `/opik-evaluate` skill.

Given a case's `assert` block, the fixture, the post-run workdir, planted.json
(from the seeder) and the agent's result.json (status / shape / cases / scoring /
experiment / scores / worst / next_step), check that the evaluation was grounded
in the seeded traces, produced an experiment with scores, named the worst items
with reasons that hit the planted failure modes, gave each judge one failure mode,
and left the app code unchanged.

No agent, no network at grade time — `verify` in run_evals.py is the one
networked step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

IGNORE = {
    ".venv",
    "__pycache__",
    "uv.lock",
    ".python-version",
    ".git",
    "result.json",
    "planted.json",
    "PROMPT.txt",
    "seed.py",
}
VALID_STATUS = {"evaluated", "blocked"}
PLANTED_KEYWORDS = ("refund", "deliver", "shipping", "legal", "lawyer", "sue", "policy")


@dataclass
class CaseResult:
    id: str
    area: str
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.checks.values())


def _files(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts) & IGNORE):
            out[str(rel)] = p.read_bytes()
    return out


def _changed(fixture: Path, workdir: Path) -> set[str]:
    fo, wo = _files(fixture), _files(workdir)
    return {r for r in set(fo) | set(wo) if fo.get(r) != wo.get(r)}


def _d(result: dict, key: str) -> dict:
    v = result.get(key)
    return v if isinstance(v, dict) else {}


def _l(result: dict, key: str) -> list:
    v = result.get(key)
    return v if isinstance(v, list) else []


def grade_case(
    case: dict,
    fixture: Path,
    workdir: Path,
    result: dict | None,
    planted: dict | None,
    area: str = "functional",
) -> CaseResult:
    a = case.get("assert", {})
    result = result or {}
    planted = planted or {}
    cases_, exp = _d(result, "cases"), _d(result, "experiment")
    scores, worst, scoring = _l(result, "scores"), _l(result, "worst"), _l(result, "scoring")
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    if a.get("experiment_url_present"):
        url = str(exp.get("url") or "")
        add("experiment_url", url.startswith("http") and bool(exp.get("id")), f"experiment={exp!r}")

    if a.get("scores_nonempty"):
        ok = any(
            isinstance(s, dict) and s.get("metric") and s.get("value") is not None for s in scores
        )
        add("scores", ok, f"scores={scores!r}")

    if a.get("cases_from_traces"):
        add(
            "cases_source",
            cases_.get("source") == "traces",
            f"cases.source={cases_.get('source')!r}",
        )

    if a.get("cases_count_min"):
        n = cases_.get("count")
        add("cases_count", isinstance(n, int) and n >= a["cases_count_min"], f"cases.count={n!r}")

    if a.get("worst_items_named"):
        ok = any(isinstance(w, dict) and w.get("reason") for w in worst)
        add("worst_named", ok, f"worst={worst[:2]!r}")

    if a.get("worst_hits_planted_failure"):
        text = " ".join(
            str(w.get("reason", "")) + " " + str(w.get("input", ""))
            for w in worst
            if isinstance(w, dict)
        ) + " ".join(str(s.get("failure_mode", "")) for s in scoring if isinstance(s, dict))
        hit = [k for k in PLANTED_KEYWORDS if k in text.lower()]
        add(
            "hits_planted",
            bool(hit),
            f"matched {hit}" if hit else "no planted failure mode in worst/scoring",
        )

    if a.get("each_judge_names_one_failure_mode"):
        judges = [
            s for s in scoring if isinstance(s, dict) and s.get("kind") in {"judge", "assertion"}
        ]
        bad = [s for s in judges if not str(s.get("failure_mode") or "").strip()]
        add("judge_failure_modes", not bad, f"judges without failure_mode: {bad!r}")

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    if status == "evaluated":
        add(
            "schema_evaluated",
            bool(exp.get("url")) and bool(scores),
            "evaluated without experiment.url + scores",
        )
    return CaseResult(id=case["id"], area=area, checks=checks)

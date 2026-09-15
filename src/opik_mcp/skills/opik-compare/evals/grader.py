# mypy: ignore-errors
"""Deterministic grader for the `/opik-compare` skill.

Given a case's `assert` block, the fixture, the post-run workdir, planted.json
(from the seeder) and the agent's result.json (status / suite / baseline /
candidate / deltas / regressions / fixes / compare_url / next_step), check that
the comparison names the planted regression and fix by dataset item id, keeps
the stable items out of both lists, links both experiments, issues no verdict,
and left the candidate code unchanged.

No agent, no network at grade time.
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
}
VALID_STATUS = {"compared", "baseline_created", "blocked"}


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


def _ids(rows) -> set[str]:
    out: set[str] = set()
    for r in rows or []:
        if isinstance(r, dict) and r.get("dataset_item_id"):
            out.add(str(r["dataset_item_id"]))
        elif isinstance(r, str):
            out.add(r)
    return out


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
    roles: dict[str, str] = planted.get("items", {})
    regressions, fixes = _ids(result.get("regressions")), _ids(result.get("fixes"))
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    base = result.get("baseline") if isinstance(result.get("baseline"), dict) else {}
    cand = result.get("candidate") if isinstance(result.get("candidate"), dict) else {}
    base_id, cand_id = str(base.get("experiment_id") or ""), str(cand.get("experiment_id") or "")

    if a.get("baseline_matches_planted"):
        want = str(planted.get("baseline_experiment_id") or "")
        add("baseline_id", bool(want) and base_id == want, f"baseline={base_id!r} planted={want!r}")

    for role in a.get("regressions_include_roles", []):
        rid = roles.get(role)
        add(
            f"regression:{role}",
            bool(rid) and rid in regressions,
            f"{role}={rid} not in regressions",
        )

    for role in a.get("fixes_include_roles", []):
        rid = roles.get(role)
        add(f"fix:{role}", bool(rid) and rid in fixes, f"{role}={rid} not in fixes")

    for role in a.get("neither_list_includes_roles", []):
        rid = roles.get(role)
        leaked = rid in regressions or rid in fixes
        add(f"stable:{role}", bool(rid) and not leaked, f"{role}={rid} leaked into a flip list")

    if a.get("deltas_nonempty"):
        deltas = result.get("deltas")
        ok = isinstance(deltas, list) and any(isinstance(d, dict) and "metric" in d for d in deltas)
        add("deltas", ok, f"deltas={deltas!r}")

    if a.get("compare_url_has_both_ids"):
        url = str(result.get("compare_url") or "")
        ok = bool(base_id) and bool(cand_id) and base_id in url and cand_id in url
        add("compare_url", ok, f"compare_url={url!r} lacks {base_id!r}/{cand_id!r}")

    if a.get("no_verdict"):
        text = " ".join(str(v).lower() for k, v in result.items() if k != "next_step")
        add(
            "no_verdict",
            "verdict" not in result and "ship it" not in text and "do not ship" not in text,
            "result carries a verdict",
        )

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    if status == "compared":
        add("schema_compared", bool(base_id) and bool(cand_id), "compared without both experiments")
    return CaseResult(id=case["id"], area=area, checks=checks)

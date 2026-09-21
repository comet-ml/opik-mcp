# mypy: ignore-errors
"""Deterministic grader for the `/opik-verify` skill.

Given a case's `assert` block, the fixture, the post-run workdir, planted.json
(from the seeder) and the agent's result.json (status / policy / criteria /
regressions / compare_url / next_step), check that the verdict matches the
planted one, the criteria table is complete, regressions are named by item id
(and only the planted ones), and the workdir is unchanged.

No agent, no network at grade time.
"""

from __future__ import annotations

import re
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
    "policy_effective.json",
}
VALID_STATUS = {"ship", "hold", "needs_review", "insufficient_evidence", "blocked"}
#: Criteria the skill must always evaluate, keyed by how the report names them.
#: A policy key maps to the criterion it drives; `evidence_strength` has no key.
POLICY_CRITERIA = {
    "min_items": "min_items",
    "max_regressions": "regressions",
    "pass_rate": "pass_rate",
    "safety_tags": "safety",
    "subgroup_key": "subgroups",
    "latency_p90_max_increase": "latency",
    "cost_per_item_max_increase": "cost",
    "judge_validated": "judge",
}
ALIASES = {
    "min_items": {"min_items", "evidence_size", "evidence size", "items"},
    "regressions": {"regressions", "max_regressions", "regression"},
    "pass_rate": {"pass_rate", "pass rate"},
    "safety": {"safety", "safety_tags"},
    "subgroups": {"subgroups", "subgroup", "subgroup_key"},
    "latency": {"latency", "latency_p90", "latency_p90_max_increase", "p90"},
    "cost": {"cost", "cost_per_item", "cost_per_item_max_increase"},
    "judge": {"judge", "judge_validated"},
}


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
    # The policy file is rewritten by `prepare`, so compare it against the staged copy.
    fo.pop("opik-release-policy.yaml", None)
    wo.pop("opik-release-policy.yaml", None)
    return {r for r in set(fo) | set(wo) if fo.get(r) != wo.get(r)}


def _criteria(result: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in result.get("criteria") or []:
        if isinstance(c, dict) and c.get("name"):
            name = str(c["name"]).lower().strip()
            for canon, names in ALIASES.items():
                if name in names or any(name.startswith(n) for n in names):
                    out[canon] = c
                    break
            else:
                out[name] = c
    return out


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
    policy: dict,
    area: str = "functional",
) -> CaseResult:
    a = case.get("assert", {})
    result = result or {}
    planted = planted or {}
    roles: dict[str, list[str]] = planted.get("items", {})
    regressions = _ids(result.get("regressions"))
    crit = _criteria(result)
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    if a.get("criteria_complete"):
        want = {
            POLICY_CRITERIA[k] for k in policy if k in POLICY_CRITERIA and policy[k] is not None
        }
        missing = sorted(want - set(crit))
        add("criteria_complete", not missing, f"criteria missing: {missing} (have {sorted(crit)})")

    for role in a.get("regressions_include_roles", []):
        want_ids = set(roles.get(role, []))
        add(
            f"regression:{role}",
            bool(want_ids) and want_ids <= regressions,
            f"{role} ids {sorted(want_ids - regressions)} not in regressions",
        )

    for role in a.get("regressions_exclude_roles", []):
        leaked = set(roles.get(role, [])) & regressions
        add(f"stable:{role}", not leaked, f"{role} ids leaked into regressions: {sorted(leaked)}")

    for role in a.get("regressions_flagged_safety_roles", []):
        rows = {
            str(r.get("dataset_item_id")): r
            for r in (result.get("regressions") or [])
            if isinstance(r, dict)
        }
        want_ids = roles.get(role, [])
        unflagged = [i for i in want_ids if not (rows.get(i) or {}).get("safety")]
        add(
            f"safety_flag:{role}",
            bool(want_ids) and not unflagged,
            f"{role} regressions without safety=true: {unflagged}",
        )

    if a.get("subgroup_observed"):
        c = crit.get("subgroups") or {}
        obs = c.get("observed")
        for group, (want_b, want_c) in a["subgroup_observed"].items():
            got = obs.get(group) if isinstance(obs, dict) else None
            nums = (
                [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(got))]
                if got is not None
                else []
            )
            ok = len(nums) >= 2 and abs(nums[0] - want_b) < 0.01 and abs(nums[1] - want_c) < 0.01
            add(
                f"subgroup:{group}",
                ok,
                f"subgroups.observed[{group}]={got!r}, want {want_b:.2f} -> {want_c:.2f}",
            )

    if a.get("no_regressions_listed"):
        add("no_regressions", not regressions, f"regressions listed: {sorted(regressions)}")

    for name in a.get("failed_criteria_include", []):
        c = crit.get(name)
        add(f"failed:{name}", c is not None and c.get("passed") is False, f"criterion {name} = {c}")

    if a.get("compare_url_has_both_ids"):
        url = str(result.get("compare_url") or "")
        b = str(planted.get("baseline_id") or "")
        cid = str(planted.get(f"{case.get('candidate')}_id") or "")
        add(
            "compare_url", bool(b) and bool(cid) and b in url and cid in url, f"compare_url={url!r}"
        )

    if a.get("policy_source"):
        src = (
            (result.get("policy") or {}).get("source")
            if isinstance(result.get("policy"), dict)
            else None
        )
        add("policy_source", src == a["policy_source"], f"policy.source={src!r}")

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    if status == "ship":
        add(
            "schema_ship",
            all(c.get("passed") for c in crit.values() if c.get("name") != "evidence_strength")
            and bool((result.get("policy") or {}).get("values", {}).get("judge_validated")),
            "ship with a failed criterion or an unvalidated judge",
        )
    if status == "hold":
        add(
            "schema_hold",
            any(c.get("passed") is False for c in crit.values()),
            "hold without a failed criterion",
        )
    return CaseResult(id=case["id"], area=area, checks=checks)

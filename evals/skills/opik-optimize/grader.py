# mypy: ignore-errors
"""Deterministic grader for the `/opik-optimize` skill.

Given a case's `assert` block, the fixture, the post-run workdir, planted.json
(from the seeder) and the agent's result.json (status / prompt / dataset / metric /
optimizer / scores / cost / run_url / next_step), check that the gain is reported
on validation, the budget was bounded, the split is real, a version was saved only
on a real improvement, and the workdir is unchanged.

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
VALID_STATUS = {"improved", "no_improvement", "blocked"}
MIN_VALIDATION = 10


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


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


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
    scores, opt, ds, prompt = (_d(result, k) for k in ("scores", "optimizer", "dataset", "prompt"))
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")
    if a.get("status_in"):
        add("status", status in a["status_in"], f"want one of {a['status_in']}, got {status}")

    if a.get("validation_reported"):
        add(
            "validation_reported",
            _num(scores.get("validation")) and _num(scores.get("initial")),
            f"scores={scores!r}",
        )

    if a.get("run_url_present"):
        url = str(result.get("run_url") or "")
        add("run_url", url.startswith("http") and "optimization" in url, f"run_url={url!r}")

    if a.get("cost_reported"):
        cost = result.get("cost")
        add("cost_reported", isinstance(cost, dict) and "llm_cost_total" in cost, f"cost={cost!r}")

    if a.get("budget_bounded"):
        ok = _num(opt.get("n_samples")) and _num(opt.get("max_trials")) and opt["max_trials"] > 0
        add("budget_bounded", ok, f"optimizer={opt!r}")

    if a.get("split_reported"):
        tr, va = _d(ds, "train"), _d(ds, "validation")
        ok = _num(tr.get("count")) and _num(va.get("count")) and va["count"] >= MIN_VALIDATION
        add("split_reported", ok, f"dataset={ds!r} (validation >= {MIN_VALIDATION} required)")

    if a.get("version_saved_iff_improved"):
        saved = bool(prompt.get("new_version"))
        want = status == "improved"
        add(
            "version_iff_improved",
            saved == want,
            f"status={status} new_version={prompt.get('new_version')!r}",
        )

    if a.get("prompt_matches_planted"):
        add(
            "prompt_matches",
            prompt.get("name") == planted.get("prompt"),
            f"prompt.name={prompt.get('name')!r} planted={planted.get('prompt')!r}",
        )

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    if status == "improved":
        add(
            "schema_improved",
            _num(scores.get("validation"))
            and _num(scores.get("initial"))
            and scores["validation"] > scores["initial"],
            "improved without validation > initial",
        )
    return CaseResult(id=case["id"], area=area, checks=checks)

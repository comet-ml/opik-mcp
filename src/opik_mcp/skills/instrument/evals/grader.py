"""Deterministic grader for the `/instrument` skill.

Given a case's `assert` block, the original fixture, and the post-run workdir,
check what the skill actually produced. Pure static analysis (ast + text) + a
file diff — no agent, no network — so it is fast and repeatable.

A live trace check (`verified` status) is out of scope here: verifying a real
trace requires the Opik SDK + a workspace and is the runner's job (it records
the status). The grader checks *instrumentation correctness* and *safety*.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

IGNORE = {".venv", "__pycache__", ".git", "uv.lock", ".python-version", "result.json"}
# Anything that looks like a real secret written into the repo (mutation smell).
SECRET_RE = re.compile(r"\b(sk-[A-Za-z0-9]{16,}|opik_[A-Za-z0-9]{16,})\b")


@dataclass
class CaseResult:
    id: str
    area: str
    status_ok: bool | None
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        checks_ok = all(ok for ok, _ in self.checks.values())
        return checks_ok and (self.status_ok is not False)


# ---------- static helpers ----------

def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if not (set(p.parts) & IGNORE)]


def _all_files(root: Path) -> set[str]:
    out = set()
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.relative_to(root).parts) & IGNORE):
            out.add(str(p.relative_to(root)))
    return out


def _func_decorators(pyfile: Path) -> dict[str, list[str]]:
    try:
        tree = ast.parse(pyfile.read_text())
    except SyntaxError:
        return {}
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = [ast.unparse(d) for d in node.decorator_list]
    return out


def _all_func_decorators(root: Path) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for f in _py_files(root):
        for name, decs in _func_decorators(f).items():
            merged.setdefault(name, []).extend(decs)
    return merged


def _track_type(dec: str) -> str | None:
    """Return the opik.track span type for a decorator string, or None if it's
    not an opik track decorator. Bare `@opik.track` / entrypoint-only -> general."""
    if "track" not in dec:
        return None
    m = re.search(r"type=['\"](\w+)['\"]", dec)
    if m:
        return m.group(1)
    return "general"


def _func_type(decs: list[str]) -> str | None:
    for d in decs:
        t = _track_type(d)
        if t:
            return t
    return None


def _text(root: Path) -> str:
    return "\n".join(p.read_text(errors="ignore") for p in _py_files(root))


def _deps(root: Path) -> str:
    parts = []
    for name in ("pyproject.toml", "requirements.txt", "package.json"):
        f = root / name
        if f.exists():
            parts.append(f.read_text(errors="ignore"))
    return "\n".join(parts)


def _changed_files(fixture: Path, workdir: Path) -> set[str]:
    changed = set()
    fo, wo = _all_files(fixture), _all_files(workdir)
    for rel in wo - fo:
        changed.add(rel)
    for rel in fo & wo:
        if (fixture / rel).read_bytes() != (workdir / rel).read_bytes():
            changed.add(rel)
    return changed


# ---------- individual checks ----------

def _check(assert_block: dict, fixture: Path, workdir: Path) -> dict[str, tuple[bool, str]]:
    decs = _all_func_decorators(workdir)
    text = _text(workdir)
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    if "language" in assert_block:
        want = assert_block["language"]
        got = "python" if _py_files(workdir) else ("other")
        add("language", got == want, f"want {want}, got {got}")

    if assert_block.get("opik_imported"):
        add("opik_imported", "import opik" in text or "from opik" in text)

    if "entrypoint" in assert_block:
        e = assert_block["entrypoint"]
        got = _func_type(decs.get(e["func"], []))
        add("entrypoint", got == e["type"], f"{e['func']}: want {e['type']}, got {got}")

    for span in assert_block.get("spans", []):
        got = _func_type(decs.get(span["func"], []))
        add(f"span:{span['func']}", got == span["type"], f"want {span['type']}, got {got}")

    if assert_block.get("flush_in_main"):
        add("flush", "flush_tracker(" in text or ".flush(" in text)

    if "dep_added" in assert_block:
        add("dep_added", assert_block["dep_added"] in _deps(workdir))

    if "integration" in assert_block:
        add("integration", f"{assert_block['integration']}(" in text)

    if "undecorated_llm_call" in assert_block:
        fn = assert_block["undecorated_llm_call"]
        got = _func_type(decs.get(fn, []))
        add("undecorated_llm_call", got is None, f"{fn} track type: {got} (want None)")

    if assert_block.get("no_double_wrap"):
        integ = "track_openai(" in text or "track_anthropic(" in text
        manual_llm = any(_func_type(d) == "llm" for d in decs.values())
        add("no_double_wrap", not (integ and manual_llm),
            "integration + manual llm span on the same path" if (integ and manual_llm) else "")

    if assert_block.get("no_prompt_migration"):
        bad = any(s in text for s in ("create_prompt", "get_prompt", "get_chat_prompt"))
        add("no_prompt_migration", not bad)

    if assert_block.get("config_untouched"):
        secrets = SECRET_RE.findall("\n".join((workdir / r).read_text(errors="ignore")
                                              for r in _all_files(workdir)))
        add("config_untouched", not secrets, f"secret-like strings: {len(secrets)}")

    if "files_changed_subset" in assert_block:
        allowed = set(assert_block["files_changed_subset"])
        changed = _changed_files(fixture, workdir)
        extra = changed - allowed
        add("minimal_diff", not extra, f"unexpected changed files: {sorted(extra)}")

    if assert_block.get("audit_only"):
        # Functions that were already tracked in the fixture keep their decorators.
        fx = _all_func_decorators(fixture)
        drift = {f: (fx[f], decs.get(f)) for f in fx
                 if _func_type(fx[f]) and _func_type(decs.get(f, [])) != _func_type(fx[f])}
        add("audit_only", not drift, f"re-instrumented already-tracked fns: {list(drift)}")

    if assert_block.get("no_modifications"):
        add("no_modifications", not _changed_files(fixture, workdir),
            f"changed: {sorted(_changed_files(fixture, workdir))}")

    return checks


def _status_ok(reported: str | None, expect) -> bool | None:
    if reported is None:
        return None
    allowed = expect if isinstance(expect, list) else [expect]
    return reported in allowed


def grade_case(case: dict, fixture: Path, workdir: Path,
               reported_status: str | None = None, area: str = "functional") -> CaseResult:
    checks = _check(case.get("assert", {}), fixture, workdir)
    return CaseResult(
        id=case["id"],
        area=area,
        status_ok=_status_ok(reported_status, case.get("expect_status")),
        checks=checks,
    )

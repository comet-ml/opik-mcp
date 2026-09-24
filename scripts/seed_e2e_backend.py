"""Seed an Opik backend with the fixture the live suite asserts against.

The live suite (``tests/live``) drives the MCP server against a real Opik and
asserts exact values, so the data has to be exactly what this file says. It
talks to the REST API with plain httpx and imports nothing from ``opik_mcp``:
the suite tests our read and write paths, and a seed that went through them
would fail with them, making a tool bug look like a fixture bug.

Every id is derived from one anchor instant and the record's key, and the
anchor is stored in the project's description. Seeding the same backend again
finds the project, rebuilds the same plan from the stored anchor, verifies the
backend still holds it, and writes nothing. ``--wipe`` deletes every record
this fixture and the suite's writes create, for a clean re-seed.

Time layout. The project summary compares a 7-day window with the 7 days
before it, and ``since``/``until`` filter on the time embedded in a UUIDv7 id,
not on ``start_time``. So each record's id is minted from its own instant. A
backend that validates id timestamps (Opik cloud does, within 24 hours) takes
``--ids-at-seed-time`` instead; the manifest then says the ids carry no time
and the window tests skip.

The fixture has a size axis as well as a content axis: a tiny, a typical, a
heavy and a wide trace, a short and a long thread, a small and a wide dataset,
a prompt with two versions and one with more than a page, and more rules and
score names than the project summary lists. Each crosses the limit it is named
for, so the suite can show what one read costs when a user has a lot of data.

Environment, the same the server reads: ``OPIK_URL`` (REST base, e.g.
``http://localhost:8080``), ``OPIK_API_KEY`` (optional for a local backend),
``OPIK_WORKSPACE`` (defaults to ``default``).

Run: ``uv run python scripts/seed_e2e_backend.py [--wipe] [--ids-at-seed-time]``
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

#: Bumped whenever the plan below changes shape. A backend seeded by another
#: version is refused rather than half-matched: rerun with ``--wipe``.
FIXTURE_VERSION = 2

#: Every name this fixture and the suite's writes create starts with this, so
#: ``--wipe`` can find them all in a shared workspace.
PREFIX = "mcp-live-"
PROJECT = f"{PREFIX}e2e"

#: What the suite's writes are named with, one run each: never the fixture's
#: prefix, so a write cannot touch what the reads assert. ``e2e-cuj-`` is the
#: prefix the shared cloud workspace's own cleanup already sweeps, so a run
#: that dies before it cleans up is swept anyway.
RUN_PREFIX = "e2e-cuj-mcp-live-"

RECENT_REGULAR = 120
PREVIOUS_REGULAR = 80
WINDOW = timedelta(days=7)
SHORT_THREADS = 10
TURNS_PER_SHORT_THREAD = 3
LONG_THREAD_TURNS = 210
WIDE_TRACE_SPANS = 250
TYPICAL_TRACE_SPANS = 5
HEAVY_BODY_CHARS = 40_000
HEAVY_SPAN_BODY_CHARS = 20_000
HEAVY_SPANS = 3
VOCABULARY_SCORE_NAMES = 30
RULES = 12
SMALL_DATASET_ITEMS = 10
WIDE_DATASET_ITEMS = 150
WIDE_DATASET_COLUMNS = 12
CHURN_PROMPT_VERSIONS = 105
REGRESSED_ITEMS = (0, 3, 6)

CORRECTNESS_STEPS = (1.0, 0.75, 0.5, 0.25, 0.0)
MODEL = "gpt-4o-mini"
PROVIDER = "openai"

#: How long derived data may take to appear: threads come from an async
#: listener, experiment scores from a debounced aggregation.
DERIVED_TIMEOUT_S = 150.0

JsonObject = dict[str, object]


# --- the manifest: what the suite may assert ---------------------------------


@dataclass(frozen=True)
class TraceCase:
    name: str
    id: str
    span_count: int


@dataclass(frozen=True)
class ThreadCase:
    id: str
    turns: int


@dataclass(frozen=True)
class DatasetCase:
    name: str
    id: str
    item_count: int
    item_ids: tuple[str, ...]
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentCase:
    name: str
    id: str


@dataclass(frozen=True)
class PromptCase:
    name: str
    version_count: int
    latest_template: str


@dataclass(frozen=True)
class IssueCase:
    id: str
    name: str
    severity: str


@dataclass(frozen=True)
class Manifest:
    """Everything the suite may assert, derived from the anchor alone."""

    fixture_version: int
    anchor: str
    ids_carry_time: bool
    project_name: str
    project_id: str
    recent_since: str
    previous_since: str
    recent_sdk_traces: int
    previous_sdk_traces: int
    recent_errors: int
    previous_errors: int
    recent_error_trace_ids: tuple[str, ...]
    low_correctness_trace_ids: tuple[str, ...]
    score_names: tuple[str, ...]
    tiny: TraceCase
    typical: TraceCase
    heavy: TraceCase
    wide: TraceCase
    short_threads: tuple[ThreadCase, ...]
    long_thread: ThreadCase
    scored_thread: ThreadCase
    thread_score_name: str
    rule_names: tuple[str, ...]
    small_dataset: DatasetCase
    wide_dataset: DatasetCase
    baseline: ExperimentCase
    candidate: ExperimentCase
    regressed_item_ids: tuple[str, ...]
    answer_prompt: PromptCase
    churn_prompt: PromptCase
    open_issue: IssueCase

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2)


# --- ids and time --------------------------------------------------------------


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid7(when: datetime, anchor: str, key: str) -> str:
    """A UUIDv7 stamped with ``when``, its random bits fixed by anchor and key.

    Deterministic so a reseed-free rerun can rebuild every id from the anchor;
    distinct across anchors so a wipe and reseed never collides with rows the
    backend still holds in a deleted state.
    """
    ms = int(when.timestamp() * 1000) & 0xFFFFFFFFFFFF
    digest = int.from_bytes(hashlib.sha256(f"{anchor}/{key}".encode()).digest()[:10], "big")
    rand_a = digest >> 68 & 0x0FFF
    rand_b = digest & 0x3FFFFFFFFFFFFFFF
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def _text(key: str, chars: int) -> str:
    """Readable filler of an exact length, different per key."""
    words = ("refund", "policy", "order", "customer", "shipping", "invoice", "eligible", "days")
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    out: list[str] = []
    length = 0
    i = 0
    while length < chars:
        word = words[(seed >> (i % 200)) % len(words)]
        out.append(word)
        length += len(word) + 1
        i += 1
    return " ".join(out)[:chars]


# --- the plan: the manifest plus the payloads that produce it ----------------


@dataclass
class Plan:
    manifest: Manifest
    traces: list[JsonObject] = field(default_factory=list)
    spans: list[JsonObject] = field(default_factory=list)
    trace_scores: list[JsonObject] = field(default_factory=list)
    thread_scores: list[JsonObject] = field(default_factory=list)
    dataset_items: dict[str, list[JsonObject]] = field(default_factory=dict)
    experiment_items: list[JsonObject] = field(default_factory=list)
    prompt_versions: list[tuple[str, str]] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    issues: list[JsonObject] = field(default_factory=list)
    all_trace_count: int = 0


class _Builder:
    def __init__(self, anchor: datetime, *, ids_carry_time: bool) -> None:
        self.anchor = anchor
        self.anchor_key = _iso(anchor)
        self.ids_carry_time = ids_carry_time
        self.traces: list[JsonObject] = []
        self.spans: list[JsonObject] = []
        self.trace_scores: list[JsonObject] = []

    def id(self, when: datetime, key: str) -> str:
        return _uuid7(when if self.ids_carry_time else self.anchor, self.anchor_key, key)

    def trace(
        self,
        key: str,
        when: datetime,
        *,
        name: str,
        duration_ms: int,
        body: JsonObject | None = None,
        thread_id: str | None = None,
        error: bool = False,
        source: str = "sdk",
    ) -> str:
        trace_id = self.id(when, f"trace/{key}")
        record: JsonObject = {
            "id": trace_id,
            "project_name": PROJECT,
            "name": name,
            "start_time": _iso(when),
            "end_time": _iso(when + timedelta(milliseconds=duration_ms)),
            "source": source,
            "input": (body or {}).get("input", {"question": f"question {key}"}),
            "output": (body or {}).get("output", {"answer": f"answer {key}"}),
            "metadata": {"fixture": key},
            "tags": ["live-e2e"],
        }
        if thread_id is not None:
            record["thread_id"] = thread_id
        if error:
            record["error_info"] = {
                "exception_type": "ToolTimeout",
                "message": f"retrieval timed out for {key}",
                "traceback": "Traceback (most recent call last): ...",
            }
        self.traces.append(record)
        return trace_id

    def span(
        self,
        trace_id: str,
        key: str,
        when: datetime,
        *,
        name: str,
        kind: str,
        duration_ms: int,
        chars: int = 40,
    ) -> None:
        record: JsonObject = {
            "id": self.id(when, f"span/{key}"),
            "trace_id": trace_id,
            "project_name": PROJECT,
            "name": name,
            "type": kind,
            "start_time": _iso(when),
            "end_time": _iso(when + timedelta(milliseconds=duration_ms)),
            "input": {"text": _text(f"{key}/in", chars)},
            "output": {"text": _text(f"{key}/out", chars)},
            "source": "sdk",
        }
        if kind == "llm":
            record |= {
                "model": MODEL,
                "provider": PROVIDER,
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            }
        self.spans.append(record)

    def turn(self, key: str, when: datetime, *, duration_ms: int, **kw: object) -> str:
        """A regular support turn: a retrieval tool span and an llm span."""
        thread_id = kw.get("thread_id")
        trace_id = self.trace(
            key,
            when,
            name="support_turn",
            duration_ms=duration_ms,
            thread_id=thread_id if isinstance(thread_id, str) else None,
            error=kw.get("error") is True,
        )
        self.span(
            trace_id,
            f"{key}/retrieve",
            when,
            name="retrieve_docs",
            kind="tool",
            duration_ms=duration_ms // 4,
        )
        self.span(
            trace_id,
            f"{key}/answer",
            when + timedelta(milliseconds=duration_ms // 4),
            name="answer",
            kind="llm",
            duration_ms=duration_ms // 2,
        )
        return trace_id

    def score(self, trace_id: str, name: str, value: float) -> None:
        self.trace_scores.append(
            {"id": trace_id, "project_name": PROJECT, "name": name, "value": value, "source": "sdk"}
        )


def build_plan(anchor: datetime, *, ids_carry_time: bool, project_id: str) -> Plan:
    """The whole fixture as data. Pure: the same anchor gives the same plan."""
    b = _Builder(anchor, ids_carry_time=ids_carry_time)
    step = timedelta(minutes=80)

    recent_errors: list[str] = []
    low_correctness: list[str] = []
    for i in range(RECENT_REGULAR):
        when = anchor - timedelta(hours=1) - i * step
        thread = None
        if i < SHORT_THREADS * TURNS_PER_SHORT_THREAD:
            thread = f"{PREFIX}thread-short-{i // TURNS_PER_SHORT_THREAD:02d}"
        error = i % 10 == 0
        trace_id = b.turn(
            f"recent/{i}", when, duration_ms=200 + (i % 50) * 10, thread_id=thread, error=error
        )
        if error:
            recent_errors.append(trace_id)
        correctness = CORRECTNESS_STEPS[i % len(CORRECTNESS_STEPS)]
        b.score(trace_id, "correctness", correctness)
        b.score(trace_id, "hallucination", 1.0 if i % 7 == 0 else 0.0)
        if correctness < 0.5:
            low_correctness.append(trace_id)
    short_threads = [
        ThreadCase(f"{PREFIX}thread-short-{n:02d}", TURNS_PER_SHORT_THREAD)
        for n in range(SHORT_THREADS)
    ]

    previous_errors = 0
    for i in range(PREVIOUS_REGULAR):
        when = anchor - WINDOW - timedelta(hours=1) - i * step
        error = i % 20 == 0
        previous_errors += error
        b.turn(f"previous/{i}", when, duration_ms=300 + (i % 50) * 10, error=error)

    # The size axis. Each case is recent and sdk-sourced, so it counts in the
    # window like any other trace; the manifest's counts include them.
    t0 = anchor - timedelta(minutes=30)
    tiny_id = b.trace(
        "tiny",
        t0,
        name="tiny",
        duration_ms=5,
        body={"input": {"q": "ping"}, "output": {"a": "pong"}},
    )
    typical_id = b.trace("typical", t0 + timedelta(seconds=1), name="typical", duration_ms=2_000)
    for s in range(TYPICAL_TRACE_SPANS):
        b.span(
            typical_id,
            f"typical/{s}",
            t0 + timedelta(seconds=1, milliseconds=100 * s),
            name=f"step-{s}",
            kind="llm" if s == TYPICAL_TRACE_SPANS - 1 else "general",
            duration_ms=100,
        )
    heavy_id = b.trace(
        "heavy",
        t0 + timedelta(seconds=2),
        name="heavy",
        duration_ms=60_000,
        body={
            "input": {"document": _text("heavy/in", HEAVY_BODY_CHARS)},
            "output": {"summary": _text("heavy/out", HEAVY_BODY_CHARS)},
        },
    )
    for s in range(HEAVY_SPANS):
        b.span(
            heavy_id,
            f"heavy/{s}",
            t0 + timedelta(seconds=2 + s),
            name=f"chunk-{s}",
            kind="llm",
            duration_ms=1_000,
            chars=HEAVY_SPAN_BODY_CHARS,
        )
    wide_id = b.trace("wide", t0 + timedelta(seconds=3), name="wide", duration_ms=30_000)
    for s in range(WIDE_TRACE_SPANS):
        b.span(
            wide_id,
            f"wide/{s}",
            t0 + timedelta(seconds=3, milliseconds=100 * s),
            name=f"tool-call-{s:03d}",
            kind="tool",
            duration_ms=50,
        )

    long_thread = ThreadCase(f"{PREFIX}thread-long", LONG_THREAD_TURNS)
    t_long = anchor - timedelta(hours=20)
    for k in range(LONG_THREAD_TURNS):
        b.trace(
            f"long/{k}",
            t_long + timedelta(seconds=2 * k),
            name="chat_turn",
            duration_ms=400,
            thread_id=long_thread.id,
        )

    # More score names than the project summary lists, on one trace.
    vocabulary = tuple(f"vocab_{n:02d}" for n in range(VOCABULARY_SCORE_NAMES))
    for n, name in enumerate(vocabulary):
        b.score(typical_id, name, n / VOCABULARY_SCORE_NAMES)

    scored_thread = short_threads[0]
    thread_score_name = "resolved"
    thread_scores: list[JsonObject] = [
        {
            "thread_id": scored_thread.id,
            "project_name": PROJECT,
            "name": thread_score_name,
            "value": 1.0,
            "source": "sdk",
        }
    ]

    recent_sdk = RECENT_REGULAR + 4 + LONG_THREAD_TURNS

    # Evaluation: a small dataset run by two experiments, the second worse on
    # known items; and a wide dataset past a page and past the column cut.
    small_name = f"{PREFIX}qa"
    small_items: list[JsonObject] = []
    small_ids: list[str] = []
    for n in range(SMALL_DATASET_ITEMS):
        item_id = b.id(anchor, f"small-item/{n}")
        small_ids.append(item_id)
        small_items.append(
            {
                "id": item_id,
                "source": "sdk",
                "data": {
                    "question": f"Can I return item {n}?",
                    "expected": f"Yes, within 30 days ({n}).",
                },
            }
        )
    wide_name = f"{PREFIX}wide"
    wide_columns = tuple(f"col_{c:02d}" for c in range(WIDE_DATASET_COLUMNS))
    wide_items: list[JsonObject] = []
    wide_ids: list[str] = []
    for n in range(WIDE_DATASET_ITEMS):
        item_id = b.id(anchor, f"wide-item/{n}")
        wide_ids.append(item_id)
        wide_items.append(
            {"id": item_id, "source": "sdk", "data": {c: f"{c} value {n}" for c in wide_columns}}
        )

    baseline = ExperimentCase(f"{PREFIX}baseline", b.id(anchor, "experiment/baseline"))
    candidate = ExperimentCase(f"{PREFIX}candidate", b.id(anchor, "experiment/candidate"))
    experiment_items: list[JsonObject] = []
    regressed: list[str] = []
    for exp in (baseline, candidate):
        for n, item_id in enumerate(small_ids):
            when = anchor - timedelta(hours=2, seconds=-n)
            trace_id = b.trace(
                f"{exp.name}/{n}",
                when,
                name="evaluation_task",
                duration_ms=500,
                source="experiment",
            )
            failed = exp is candidate and n in REGRESSED_ITEMS
            b.score(trace_id, "correctness", 0.0 if failed else 1.0)
            if failed:
                regressed.append(item_id)
            experiment_items.append(
                {
                    "id": b.id(anchor, f"experiment-item/{exp.name}/{n}"),
                    "experiment_id": exp.id,
                    "dataset_item_id": item_id,
                    "trace_id": trace_id,
                }
            )

    answer_prompt = PromptCase(f"{PREFIX}answer", 2, "Answer {{question}} using {{policy}}.")
    churn_prompt = PromptCase(
        f"{PREFIX}churn",
        CHURN_PROMPT_VERSIONS,
        f"Revision {CHURN_PROMPT_VERSIONS - 1}: answer {{{{question}}}}.",
    )
    prompt_versions = [
        (answer_prompt.name, "Answer {{question}}."),
        (answer_prompt.name, answer_prompt.latest_template),
    ]
    prompt_versions += [
        (churn_prompt.name, f"Revision {n}: answer {{{{question}}}}.")
        for n in range(CHURN_PROMPT_VERSIONS)
    ]

    rules = [f"{PREFIX}judge-{n:02d}" for n in range(RULES)]

    open_issue = IssueCase(
        b.id(anchor, "issue/open"), "Refund answers cite a policy that does not exist", "high"
    )
    issues: list[JsonObject] = [
        {
            "id": case.id,
            "name": case.name,
            "severity": case.severity,
            "description": f"{case.name}.",
            "cause": "Seeded by the live suite.",
            "suggested_fix": "None; this is fixture data.",
            "count": count,
            "total_count": 100,
            "users_impacted": count // 2,
            "total_users": 40,
        }
        for case, count in ((open_issue, 12),)
    ]

    manifest = Manifest(
        fixture_version=FIXTURE_VERSION,
        anchor=_iso(anchor),
        ids_carry_time=ids_carry_time,
        project_name=PROJECT,
        project_id=project_id,
        recent_since=_iso(anchor - WINDOW),
        previous_since=_iso(anchor - 2 * WINDOW),
        recent_sdk_traces=recent_sdk,
        previous_sdk_traces=PREVIOUS_REGULAR,
        recent_errors=len(recent_errors),
        previous_errors=previous_errors,
        recent_error_trace_ids=tuple(recent_errors),
        low_correctness_trace_ids=tuple(low_correctness),
        score_names=tuple(sorted({"correctness", "hallucination", thread_score_name, *vocabulary})),
        tiny=TraceCase("tiny", tiny_id, 0),
        typical=TraceCase("typical", typical_id, TYPICAL_TRACE_SPANS),
        heavy=TraceCase("heavy", heavy_id, HEAVY_SPANS),
        wide=TraceCase("wide", wide_id, WIDE_TRACE_SPANS),
        short_threads=tuple(short_threads),
        long_thread=long_thread,
        scored_thread=scored_thread,
        thread_score_name=thread_score_name,
        rule_names=tuple(rules),
        small_dataset=DatasetCase(
            small_name, "", SMALL_DATASET_ITEMS, tuple(small_ids), ("question", "expected")
        ),
        wide_dataset=DatasetCase(wide_name, "", WIDE_DATASET_ITEMS, tuple(wide_ids), wide_columns),
        baseline=baseline,
        candidate=candidate,
        regressed_item_ids=tuple(regressed),
        answer_prompt=answer_prompt,
        churn_prompt=churn_prompt,
        open_issue=open_issue,
    )
    return Plan(
        manifest=manifest,
        traces=b.traces,
        spans=b.spans,
        trace_scores=b.trace_scores,
        thread_scores=thread_scores,
        dataset_items={small_name: small_items, wide_name: wide_items},
        experiment_items=experiment_items,
        prompt_versions=prompt_versions,
        rules=rules,
        issues=issues,
        all_trace_count=len(b.traces),
    )


# --- the backend ---------------------------------------------------------------


class SeedError(RuntimeError):
    """The backend refused a write, or does not hold what the plan says."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Backend:
    """The REST calls the seed makes. Its own, so it shares no code under test."""

    def __init__(self, base_url: str, workspace: str, api_key: str | None) -> None:
        headers = {"Comet-Workspace": workspace}
        if api_key:
            headers["Authorization"] = api_key
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=60.0)

    @classmethod
    def from_env(cls) -> Backend:
        base = os.environ.get("OPIK_URL")
        if not base:
            raise SeedError("OPIK_URL is not set: point it at the Opik REST base.")
        workspace = os.environ.get("OPIK_WORKSPACE") or "default"
        return cls(base, workspace, os.environ.get("OPIK_API_KEY") or None)

    def close(self) -> None:
        self._http.close()

    def call(self, method: str, path: str, body: object = None, **params: str) -> object:
        response = self._http.request(
            method, f"/v1/private{path}", json=body, params=params or None
        )
        if response.status_code >= 400:
            raise SeedError(
                f"{method} {path} answered {response.status_code}: {response.text[:500]}",
                status=response.status_code,
            )
        if not response.content:
            return None
        parsed: object = response.json()
        return parsed

    def page(self, path: str, **params: str) -> tuple[list[JsonObject], int]:
        body = _as_object(self.call("GET", path, **params))
        content = body.get("content")
        rows = [_as_object(row) for row in content] if isinstance(content, list) else []
        total = body.get("total")
        return rows, total if isinstance(total, int) else len(rows)

    def ready(self) -> bool:
        try:
            response = self._http.get("/health-check", params={"name": "all", "type": "ready"})
        except httpx.HTTPError:
            return False
        return response.status_code == 200


def _as_object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise SeedError(f"expected a JSON object, got {type(value).__name__}")
    return {str(k): v for k, v in value.items()}


def _chunks(rows: Sequence[JsonObject], size: int) -> Iterator[list[JsonObject]]:
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def _wait(what: str, check: Callable[[], bool], timeout: float = DERIVED_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise SeedError(f"{what} did not appear within {timeout:.0f}s")
        time.sleep(1.0)


def _description(anchor: str, *, ids_carry_time: bool) -> str:
    ids = "record" if ids_carry_time else "seed"
    return f"opik-mcp live fixture v{FIXTURE_VERSION} anchor={anchor} ids={ids}"


def _parse_description(text: str) -> tuple[int, datetime, bool] | None:
    parts = dict(p.split("=", 1) for p in text.split() if "=" in p)
    version = text.split(" v", 1)[1].split()[0] if " v" in text else ""
    if not version.isdigit() or "anchor" not in parts or "ids" not in parts:
        return None
    anchor = datetime.fromisoformat(parts["anchor"].replace("Z", "+00:00"))
    return int(version), anchor, parts["ids"] == "record"


def _find_project(backend: Backend) -> JsonObject | None:
    rows, _ = backend.page("/projects", name=PROJECT, size="100")
    return next((row for row in rows if row.get("name") == PROJECT), None)


def _by_name(backend: Backend, path: str, name: str) -> JsonObject:
    rows, _ = backend.page(path, name=name, size="100")
    row = next((r for r in rows if r.get("name") == name), None)
    if row is None:
        raise SeedError(f"{path} holds no record named {name!r}; rerun with --wipe")
    return row


# --- seeding -------------------------------------------------------------------


def _write(backend: Backend, plan: Plan) -> None:
    m = plan.manifest
    for batch in _chunks(plan.traces, 500):
        backend.call("POST", "/traces/batch", {"traces": batch})
    for batch in _chunks(plan.spans, 500):
        backend.call("POST", "/spans/batch", {"spans": batch})
    for batch in _chunks(plan.trace_scores, 500):
        backend.call("PUT", "/traces/feedback-scores", {"scores": batch})
    # Threads are built by an async listener: wait for every one verify reads,
    # and for the long one to hold all its turns.
    for thread in (m.scored_thread,):
        _wait(f"thread {thread.id}", functools.partial(_thread_exists, backend, thread.id))
    _wait(
        f"all turns of {m.long_thread.id}",
        lambda: (
            (_thread(backend, m.long_thread.id) or {}).get("number_of_messages")
            == 2 * m.long_thread.turns
        ),
    )
    backend.call("PUT", "/traces/threads/feedback-scores", {"scores": plan.thread_scores})

    for dataset, items in plan.dataset_items.items():
        backend.call("POST", "/datasets", {"name": dataset, "description": "live suite"})
        for batch in _chunks(items, 500):
            backend.call("PUT", "/datasets/items", {"dataset_name": dataset, "items": batch})
    for exp in (m.baseline, m.candidate):
        backend.call(
            "POST",
            "/experiments",
            {
                "id": exp.id,
                "name": exp.name,
                "dataset_name": m.small_dataset.name,
                "project_name": PROJECT,
            },
        )
    backend.call("POST", "/experiments/items", {"experiment_items": plan.experiment_items})

    for name, template in plan.prompt_versions:
        backend.call("POST", "/prompts/versions", {"name": name, "version": {"template": template}})

    for rule in plan.rules:
        backend.call(
            "POST",
            "/automations/evaluators/",
            {
                "name": rule,
                "type": "llm_as_judge",
                "action": "evaluator",
                "project_ids": [m.project_id],
                "sampling_rate": 1.0,
                "enabled": True,
                "code": {
                    "model": {"name": MODEL, "temperature": 0.0},
                    "messages": [{"role": "USER", "content": "Is {{output}} correct?"}],
                    "variables": {"output": "output"},
                    "schema": [{"name": "judged", "type": "BOOLEAN", "description": "correct"}],
                },
            },
        )
    backend.call(
        "POST",
        "/agent-insights/issues",
        {
            "project_id": m.project_id,
            "report_day": m.anchor[:10],
            "issues": plan.issues,
        },
    )


def _thread(backend: Backend, thread_id: str) -> JsonObject | None:
    try:
        found = backend.call(
            "POST", "/traces/threads/retrieve", {"project_name": PROJECT, "thread_id": thread_id}
        )
    except SeedError as err:
        # Not there yet is a 404; anything else is a real failure, said now.
        if err.status == 404:
            return None
        raise
    return _as_object(found)


def _thread_exists(backend: Backend, thread_id: str) -> bool:
    return _thread(backend, thread_id) is not None


def _resolve_ids(backend: Backend, manifest: Manifest) -> Manifest:
    """Fill in the ids the backend assigns itself: the datasets'."""
    small = _by_name(backend, "/datasets", manifest.small_dataset.name)
    wide = _by_name(backend, "/datasets", manifest.wide_dataset.name)
    return dataclasses.replace(
        manifest,
        small_dataset=dataclasses.replace(manifest.small_dataset, id=str(small["id"])),
        wide_dataset=dataclasses.replace(manifest.wide_dataset, id=str(wide["id"])),
    )


def verify(backend: Backend, plan: Plan) -> list[str]:
    """What the backend does not hold of the plan. Empty means it all landed."""
    m = plan.manifest
    problems: list[str] = []

    def expect(what: str, actual: object, *, wanted: object) -> None:
        if actual != wanted:
            problems.append(f"{what}: backend has {actual!r}, plan says {wanted!r}")

    _, traces = backend.page("/traces", project_id=m.project_id, size="1")
    expect("traces in the project", traces, wanted=plan.all_trace_count)
    wide = _as_object(backend.call("GET", f"/traces/{m.wide.id}"))
    expect("spans on the wide trace", wide.get("span_count"), wanted=m.wide.span_count)
    long_thread = _thread(backend, m.long_thread.id) or {}
    expect(
        "messages in the long thread",
        long_thread.get("number_of_messages"),
        wanted=2 * m.long_thread.turns,
    )
    scored = _thread(backend, m.scored_thread.id) or {}
    scores = scored.get("feedback_scores")
    names = (
        {s.get("name") for s in scores if isinstance(s, dict)}
        if isinstance(scores, list)
        else set()
    )
    expect("score on the scored thread", m.thread_score_name in names, wanted=True)
    for dataset in (m.small_dataset, m.wide_dataset):
        _, items = backend.page(f"/datasets/{dataset.id}/items", size="1")
        expect(f"items in {dataset.name}", items, wanted=dataset.item_count)
    for prompt in (m.answer_prompt, m.churn_prompt):
        row = _by_name(backend, "/prompts", prompt.name)
        _, versions = backend.page(f"/prompts/{row['id']}/versions", size="1")
        expect(f"versions of {prompt.name}", versions, wanted=prompt.version_count)
    _, rules = backend.page("/automations/evaluators/", project_id=m.project_id, size="1")
    expect("online rules", rules, wanted=len(m.rule_names))
    issues, _ = backend.page("/agent-insights/issues", project_id=m.project_id, size="100")
    expect(
        "Diagnostics issues",
        sorted(str(i.get("id")) for i in issues),
        wanted=[m.open_issue.id],
    )
    expect(
        "status of the Diagnostics issues",
        sorted(str(i.get("status")) for i in issues),
        wanted=["open"],
    )
    for exp in (m.baseline, m.candidate):
        row = _as_object(backend.call("GET", f"/experiments/{exp.id}"))
        expect(f"items in {exp.name}", row.get("trace_count"), wanted=m.small_dataset.item_count)
    return problems


def _experiment_scores_ready(backend: Backend, manifest: Manifest) -> bool:
    for exp in (manifest.baseline, manifest.candidate):
        row = _as_object(backend.call("GET", f"/experiments/{exp.id}"))
        scores = row.get("feedback_scores")
        if not isinstance(scores, list) or not scores:
            return False
    return True


def _finish(backend: Backend, plan: Plan) -> Manifest:
    plan.manifest = _resolve_ids(backend, plan.manifest)
    _wait("experiment scores", lambda: _experiment_scores_ready(backend, plan.manifest))
    problems = verify(backend, plan)
    if problems:
        raise SeedError(
            "the backend does not hold the fixture (rerun with --wipe):\n- " + "\n- ".join(problems)
        )
    return plan.manifest


def load(backend: Backend) -> Manifest | None:
    """The fixture already on the backend, verified; None when there is none.

    Never writes, so it is what a run against a shared workspace calls: the
    fixture there is seeded once by hand and never touched again.
    """
    existing = _find_project(backend)
    if existing is None:
        return None
    stored = _parse_description(str(existing.get("description") or ""))
    if stored is None or stored[0] != FIXTURE_VERSION:
        raise SeedError(
            f"project {PROJECT!r} exists but was not seeded by fixture "
            f"v{FIXTURE_VERSION}; rerun with --wipe"
        )
    _, anchor, carry = stored
    return _finish(
        backend, build_plan(anchor, ids_carry_time=carry, project_id=str(existing["id"]))
    )


def seed(backend: Backend, *, ids_carry_time: bool = True, now: datetime | None = None) -> Manifest:
    """Find the fixture or write it, then prove the backend holds it."""
    found = load(backend)
    if found is not None:
        return found
    anchor = (now or datetime.now(UTC)).replace(microsecond=0)
    description = _description(_iso(anchor), ids_carry_time=ids_carry_time)
    backend.call("POST", "/projects", {"name": PROJECT, "description": description})
    project = _find_project(backend)
    if project is None:
        raise SeedError(f"created project {PROJECT!r} but cannot find it")
    plan = build_plan(anchor, ids_carry_time=ids_carry_time, project_id=str(project["id"]))
    _write(backend, plan)
    return _finish(backend, plan)


# --- wiping --------------------------------------------------------------------


def _named(
    backend: Backend, path: str, prefix: str, older_than: datetime | None
) -> list[JsonObject]:
    rows, _ = backend.page(path, name=prefix, size="100")
    return [
        r
        for r in rows
        if str(r.get("name", "")).startswith(prefix)
        and (older_than is None or _created(r) < older_than)
    ]


def _created(row: JsonObject) -> datetime:
    raw = str(row.get("created_at") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.max.replace(tzinfo=UTC)


def delete_named(backend: Backend, prefix: str, *, older_than: datetime | None = None) -> list[str]:
    """Delete every project, experiment, dataset and prompt named with ``prefix``.

    ``older_than`` keeps what was created after it, so a sweep for a crashed
    run's leftovers never takes a run that is still going. Each pass deletes
    one page and asks again, until nothing named with the prefix is left.
    Returns what went.
    """
    gone: list[str] = []
    projects = functools.partial(_named, backend, "/projects", prefix, older_than)
    for page in _pages(projects, what="projects"):
        for project in page:
            for rules in _pages(
                functools.partial(_rules, backend, str(project["id"])), what="online rules"
            ):
                backend.call(
                    "POST", "/automations/evaluators/delete", {"ids": [r["id"] for r in rules]}
                )
            backend.call("DELETE", f"/projects/{project['id']}")
            gone.append(f"project {project['name']}")
    for kind, path, delete_path in (
        ("experiment", "/experiments", "/experiments/delete"),
        ("dataset", "/datasets", "/datasets/delete-batch"),
        ("prompt", "/prompts", "/prompts/delete"),
    ):
        named = functools.partial(_named, backend, path, prefix, older_than)
        for mine in _pages(named, what=f"{kind}s"):
            backend.call("POST", delete_path, {"ids": [r["id"] for r in mine]})
            gone += [f"{kind} {r['name']}" for r in mine]
    return gone


def wipe(backend: Backend) -> list[str]:
    """Delete the fixture and every run's writes."""
    return delete_named(backend, PREFIX) + delete_named(backend, RUN_PREFIX)


def sweep_runs(backend: Backend, *, older_than: timedelta) -> list[str]:
    """Delete what runs that died before cleaning up left behind."""
    return delete_named(backend, RUN_PREFIX, older_than=datetime.now(UTC) - older_than)


def _rules(backend: Backend, project_id: str) -> list[JsonObject]:
    return backend.page("/automations/evaluators/", project_id=project_id, size="100")[0]


def _pages(fetch: Callable[[], list[JsonObject]], *, what: str) -> Iterator[list[JsonObject]]:
    """Pages to delete, until one comes back empty.

    Refuses to go round again on the same ids: a delete the backend accepted
    but did not apply would otherwise loop forever on a shared workspace.
    """
    seen: set[str] = set()
    while rows := fetch():
        ids = {str(r.get("id")) for r in rows}
        if ids <= seen:
            raise SeedError(f"the backend still lists {len(ids)} {what} after deleting them")
        seen |= ids
        yield rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--wipe", action="store_true", help="delete the fixture, then exit")
    parser.add_argument(
        "--ids-at-seed-time",
        action="store_true",
        help="mint every id at seeding time, for a backend that validates id timestamps",
    )
    args = parser.parse_args(argv)
    backend = Backend.from_env()
    try:
        if args.wipe:
            for line in wipe(backend):
                print(f"deleted {line}", file=sys.stderr)
            return 0
        manifest = seed(backend, ids_carry_time=not args.ids_at_seed_time)
    except SeedError as err:
        print(f"seed failed: {err}", file=sys.stderr)
        return 1
    finally:
        backend.close()
    print(manifest.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())

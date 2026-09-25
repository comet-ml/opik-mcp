"""What the live suite's seed writes, checked without a backend.

The seed runs against a shared cloud workspace on every nightly, so its plan
must stay inside the names it was given: everything it writes carries the
run's prefix, which is also all the run deletes afterwards. And cloud refuses
an id more than about a day old, so a cloud-sized window must keep every id
inside that. Both are properties of the pure plan, so they are checked here,
in ``make check``, rather than discovered by a nightly that fails at 4 a.m.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from scripts.seed_e2e_backend import (
    RUN_PREFIX,
    WINDOW,
    Plan,
    build_plan,
    default_window,
    owns,
)

ANCHOR = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
PREFIX = "e2e-cuj-mcp-live-test-fx"


def _plan(window: timedelta = WINDOW) -> Plan:
    return build_plan(ANCHOR, window=window, project_id="project-id", prefix=PREFIX)


def _minted(record_id: object) -> datetime:
    """The instant inside a UUIDv7, which is what since/until filter on."""
    ms = uuid.UUID(str(record_id)).int >> 80
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def test_every_record_the_seed_writes_lives_under_its_prefix() -> None:
    plan = _plan()
    projects = {str(r["project_name"]) for r in (*plan.traces, *plan.spans, *plan.trace_scores)} | {
        str(s["project_name"]) for s in plan.thread_scores
    }
    named = [
        *plan.dataset_items,
        plan.manifest.baseline.name,
        plan.manifest.candidate.name,
        *(name for name, _ in plan.prompt_versions),
        *(str(rule["name"]) for rule in plan.rules),
    ]
    outside = [name for name in named if not name.startswith(PREFIX)]
    assert (projects, outside) == ({PREFIX}, [])


def test_a_local_backend_gets_a_week_and_any_other_a_cloud_window() -> None:
    assert (default_window("http://localhost:8080"), default_window("http://127.0.0.1:28080")) == (
        WINDOW,
        WINDOW,
    )
    assert default_window("https://www.comet.com/opik/api") < WINDOW


def test_a_cloud_window_keeps_every_id_under_a_day_old() -> None:
    # The window a run against cloud actually uses, not a constant beside it.
    plan = _plan(default_window("https://www.comet.com/opik/api"))
    minted = [_minted(r["id"]) for r in (*plan.traces, *plan.spans)]
    oldest = ANCHOR - min(minted)
    assert oldest < timedelta(hours=24), f"the oldest id is {oldest} old; cloud refuses it"


def test_the_two_windows_hold_exactly_the_traces_the_manifest_counts() -> None:
    plan = _plan()
    m = plan.manifest
    recent_since = datetime.fromisoformat(m.recent_since)
    previous_since = datetime.fromisoformat(m.previous_since)
    sdk = [_minted(t["id"]) for t in plan.traces if t["source"] == "sdk"]
    recent = sum(recent_since <= when <= ANCHOR for when in sdk)
    previous = sum(previous_since <= when < recent_since for when in sdk)
    assert (recent, previous) == (m.recent_sdk_traces, m.previous_sdk_traces)


def test_the_seeded_online_rules_are_created_disabled() -> None:
    # Enabled, a cloud workspace with a provider key would run every LLM judge
    # on every seeded trace: real cost, and score names the tests do not expect.
    assert {rule["enabled"] for rule in _plan().rules} == {False}


def test_a_prefix_owns_whole_names_only() -> None:
    # Owning is what a wipe deletes: "my-fixture" must never take "my-fixture2".
    names = ["my-fixture", "my-fixture-qa", "my-fixture2", "my-fixture-old-qa", "other"]
    assert [n for n in names if owns("my-fixture", n)] == [
        "my-fixture",
        "my-fixture-qa",
        "my-fixture-old-qa",
    ]


def test_the_run_prefix_owns_every_run_and_nothing_else() -> None:
    names = [f"{RUN_PREFIX}123-abcdef-fx", f"{RUN_PREFIX}9-000000-w", "e2e-cuj-other-suite"]
    assert [n for n in names if owns(RUN_PREFIX, n)] == names[:2]

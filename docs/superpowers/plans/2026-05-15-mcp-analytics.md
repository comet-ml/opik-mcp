# opik-mcp Analytics — Design & Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument `opik-mcp` to emit product-analytics events so the team can make a data-driven Phase-2 go/no-go call (per `docs/team-brief.md`). Events must land in the same Comet-stats stream that powers Segment → PostHog → Metabase, so they sit alongside the existing `opik_*` event vocabulary used by `opik-backend` and `opik-frontend`.

**Non-goals:**
- Customer-facing usage dashboards (deferred per ADR 0006).
- Operational metrics for SRE (Prometheus / Grafana) — separate workstream; see `design.md §2.11`.
- Tracing the user's natural-language input through analytics (never sent — privacy).
- Real-time billing / quota enforcement (Phase 2 concern, not analytics).

---

## 1. Why this matters

Phase 1 ships as `uvx opik-mcp` running on the user's laptop. The team-brief asks "Phase 2 or no Phase 2?" The decision hinges on four observable signals:

| Signal | Question it answers | Drives… |
|---|---|---|
| **Adoption** | How many installs are there? Which hosts? | Whether to invest in OAuth + hosted ($-effort) Phase 2 |
| **Activation** | Do installs actually call tools? Do they reach `ask_ollie`? | Whether the tool surface is discoverable |
| **Engagement** | Which tools, how often, with what shape? | Whether to keep all 11 tools or trim (ADR 0004 revisit) |
| **Reliability** | Where do calls fail? Cold-start? Auth? Stream truncation? | Where to invest in robustness before scaling |

Without telemetry, the team has to ask each beta user manually. With it, every install becomes a data point.

---

## 2. Transport: how we get events to Comet stats

### 2.1 Decision — direct POST from `opik-mcp` to the Comet stats endpoint

Three options were considered:

| Option | Description | Pick? |
|---|---|---|
| **A. Direct** | Mirror Java's `StatsClient`: async POST `{anonymous_id, event_type, event_properties}` to `https://stats.comet.com/notify/event/`. | ✅ |
| **B. Proxy via opik-backend** | New `POST /v1/private/analytics/track` endpoint that calls `analyticsService.trackEvent`. | ❌ |
| **C. Via opik Python SDK** | Add a `client.track_event(...)` API to `opik` SDK and reuse from MCP. | ❌ |

**Rationale for A:**
- Matches the Phase-1 constraint "zero changes in opik-backend" (`docs/phase-1.md`, `docs/team-brief.md`).
- Mirrors the well-established backend pattern (`apps/opik-backend/.../bi/StatsClient.java`) — one source of truth for the wire format. If the backend pattern changes, we copy the change; no second integration to maintain.
- Phase-1 distribution is `uvx opik-mcp` on the user's laptop — keeping the MCP self-contained means exactly one outbound destination for telemetry, which is the easiest thing to document, audit, and disable.
- Phase-2 (hosted) can drop in the same transport unchanged, then later migrate to the backend-side proxy with a one-flag swap (we keep the call shape identical).

### 2.2 Wire format (matches `apps/opik-backend/.../bi/BiEvent.java`)

```http
POST https://stats.comet.com/notify/event/
Content-Type: application/json
Accept: application/json

{
  "anonymous_id": "<workspace user_name | installation uuid>",
  "event_type": "opik_mcp_<name>",
  "event_properties": {
    "environment": "prod",
    "workspace_id": "<workspace>",
    "opik_mcp_version": "0.0.1",
    ...
  }
}
```

Response: `{"success": bool, "message": string}` — we don't care what's in it; we log on non-`success` and move on.

### 2.3 Fire-and-forget contract (copied verbatim from the Java contract)

The Python client MUST:
- Never throw out of the public `track_event(...)` call.
- Never block the caller — enqueue on a `queue.Queue` (stdlib, thread-safe) and return immediately.
- Cap the in-flight queue at 100 events. Drop with a debug log when full — analytics never backpressure tool calls.
- Apply a connect timeout (5 s) and total request timeout (10 s).
- On *any* exception in the worker, log at `WARNING` and continue.
- Be safe to call from sync code AND from any async context, including `__main__.main()` before `mcp.run()` (no running event loop yet).

**Worker model — daemon thread, not asyncio task.** The original draft used `asyncio.Queue` + `asyncio.create_task`, but that requires a running event loop at the time `track_event` is called. Two paths break that assumption: `__main__.main()` emits `server_started` *before* `mcp.run()` ever starts the loop; and `tests/` exercise call sites in pure sync code. A daemon `threading.Thread` consuming a `queue.Queue` with a sync `httpx.Client` works from anywhere, has no loop dependency, and exits silently when the interpreter shuts down. The performance cost is negligible — analytics is at most a few events per minute.

### 2.4 Configuration knobs

Add to `src/opik_mcp/config.py`:

```python
opik_mcp_analytics_enabled: bool = True
opik_mcp_analytics_url: str = "https://stats.comet.com/notify/event/"
opik_mcp_analytics_environment: str = "prod"   # "prod" / "staging" / "dev"
opik_mcp_analytics_connect_timeout_s: float = 5.0
opik_mcp_analytics_total_timeout_s: float = 10.0
```

Env-var names match the pydantic-settings convention (`OPIK_MCP_ANALYTICS_*`). Default on — same posture as `opik-backend`'s `OPIK_USAGE_REPORT_ENABLED=true`. The README must document the off-switch (`OPIK_MCP_ANALYTICS_ENABLED=false`) prominently — this is privacy posture.

Why a dedicated `OPIK_MCP_ANALYTICS_ENABLED` rather than reusing `OPIK_USAGE_REPORT_ENABLED`: the user installing `opik-mcp` may already have `opik-backend` running with `OPIK_USAGE_REPORT_ENABLED=false`. We don't want to silently inherit; we want explicit consent for the MCP surface.

---

## 3. Identity model

| Caller context | `anonymous_id` |
|---|---|
| Configured `COMET_WORKSPACE` + a request was authenticated | `<workspace>` (slug, matches `Comet-Workspace` header) |
| MCP server started but never authenticated | A stable per-install UUID v4, persisted at `~/.opik-mcp/install-id` (mirrors `usageReport.anonymousId` in the Java side) |
| Both available | We prefer **workspace** — it's the same identity backend events ship and groups by org. The install UUID is the floor when there is nothing else. |

**Why not `user_name`?** Phase 1 has no user-identity propagation — the local MCP server only knows the API key, which `opik-backend` resolves into a user but `opik-mcp` doesn't get back. We *do* get `workspace` from `COMET_WORKSPACE`. Phase 2 will add user identity from the OAuth JWT (`sub` claim).

**Persisted install UUID:** Generated on first start, written to `~/.opik-mcp/install-id` with mode `0600`. Cached in-process for the lifetime of the run. This matches the backend's `MetadataDAO` ANONYMOUS_ID row, just file-backed.

---

## 4. Event taxonomy

### 4.1 Naming convention

- Prefix all events `opik_mcp_` (specific to this surface — easy to filter against backend's `opik_*` event population).
- snake_case.
- All property values serialize as strings (matches `Map<String, String>` on the Java side — Segment-friendly).
- Numeric values that matter for analysis become strings of decimal digits (e.g., `"duration_ms": "1340"`); analytics-side parses them. Same convention as `traces_count` in `BiEventListener.java`.

### 4.2 Common properties on every event

Stamped in the analytics client, not at each call site:

| Property | Source | Example |
|---|---|---|
| `environment` | `OPIK_MCP_ANALYTICS_ENVIRONMENT` | `"prod"` |
| `opik_mcp_version` | `importlib.metadata.version("opik-mcp")` | `"0.0.1"` |
| `transport` | `Settings.opik_mcp_transport` | `"stdio"` / `"http"` |
| `workspace_id` | `Settings.comet_workspace` if set | `"my-workspace"` |
| `install_id` | persisted UUID | `"7c…"` |
| `python_version` | `sys.version_info` formatted | `"3.13.1"` |
| `platform` | `platform.system()` | `"Darwin"` / `"Linux"` |

### 4.3 The events

#### Lifecycle (low-cardinality, high-signal)

**`opik_mcp_server_started`** — emitted once at `__main__.main()` boot.
- `transport`, `analytics_enabled` (sanity), `has_workspace`, `has_api_key`, `has_default_project`.

**`opik_mcp_session_initialized`** — emitted from a FastMCP `initialize` hook, once per MCP session (host connect).
- `mcp_host` (`clientInfo.name` from initialize params, e.g. `"claude-code"`, `"cursor"`, `"vscode-copilot"`),
- `mcp_client_version` (`clientInfo.version`),
- `mcp_protocol_version` (e.g. `"2025-11-25"`),
- `capabilities` (compact string: `"elicitation,tasks"`).

#### Tool calls (the workhorse event)

**`opik_mcp_tool_called`** — emitted from a thin wrapper around every `@mcp.tool` function, on exit (success or failure).
- `tool_name` (`"read" | "list" | "ask_ollie" | "score" | "comment" | "hello"`),
- `success` (`"true"` / `"false"`),
- `error_kind` (only present on failure: `"missing_config"`, `"comet_auth_failed"`, `"ollie_not_enabled"`, `"comet_protocol_error"`, `"opik_http_4xx"`, `"opik_http_5xx"`, `"ollie_stream_error"`, `"pod_warmup_timeout"`, `"unknown"`),
- `duration_ms` (string of int),
- per-tool extras (low-cardinality, no payloads — see 4.4 below).

This single event is enough to answer "which tools get used, how often, by whom, with what success" — which covers most of the Phase-2 question.

#### `ask_ollie`-specific (the most expensive call, deserves its own enrichment)

**`opik_mcp_ask_ollie_completed`** — emitted at the end of `run_ask_ollie`, alongside the generic `tool_called` (both fire; analytics-side can pick).
- `success`, `error_kind?`, `total_duration_ms`,
- `pod_warmup_ms` (time spent in `wait_ready` before `create_session`),
- `time_to_first_event_ms`,
- `event_count` (the `events_seen` counter — total SSE frames seen),
- `had_continuation` (`thread_id` was passed in),
- `had_page_context`, `had_project_name`, `attach_resources_count`,
- `completion_state` (`"message_end"`, `"cancelled"`, `"truncated"`, `"error"`),
- `auto_approvals_count` (number of `confirm_required` events the YOLO path auto-yes'd),
- `auto_approval_tools` (comma-joined sorted unique target tool names from the auto-approvals).

#### Audit bridge

**`opik_mcp_auto_approval`** — emitted alongside each `audit.write_auto_approval(...)` call.
- `tool` (always `"ask_ollie"` today),
- `target_tool` (the Ollie sub-tool we approved — e.g. `"add_score"`),
- `had_summary` (`"true"` / `"false"`).

We send count signal, not the row content — the local audit log already has the full row.

### 4.4 Per-tool extras for `opik_mcp_tool_called`

Carefully chosen to be low-cardinality. **Never** ship free text.

| Tool | Extra properties |
|---|---|
| `read` | `entity_type`, `id_kind` (`"uuid"` / `"name"` / `"uri"`), `compression_tier` (`"FULL"` / `"MEDIUM"` / `"SKELETON"`), `returned_tokens_bucket` (`"<2k"` / `"2k-8k"` / `"8k-32k"` / `">32k"`). |
| `list` | `entity_type`, `had_name_filter`, `page`, `size`, `returned_count_bucket`. |
| `ask_ollie` | See §4.3 dedicated event. The `tool_called` event still fires with `success` + `error_kind` + `duration_ms` for surface uniformity. |
| `score` | `target_type` (`"trace"` / `"span"` / `"thread"`), `score_name_bucket` (`"helpfulness"`, `"hallucination"`, `"tone"`, `"other"`), `has_reason`, `has_category`. |
| `comment` | `target_type`, `text_length_bucket` (`"<100"` / `"100-1000"` / `">1000"`). |
| `hello` | `name_was_default` (`"true"` if `name == "world"`). |

The "bucket" pattern is deliberate: it gives us actionable distributions without identifying individuals (cf. the [tokens-bucket pattern](https://posthog.com/docs/data/anonymization)).

### 4.5 What we **never** send (privacy guarantees, written down)

| Field | Why we strip it |
|---|---|
| `query` (ask_ollie) | Free-form user prose. |
| `page_context` (ask_ollie) | Free-form user prose / UI snapshot. |
| `text` (comment) | Free-form user prose. |
| `reason` (score) | Free-form user prose. |
| `template` (save_prompt_version, Phase-2) | Prompt content — IP-sensitive. |
| `name` (entity names like project_name, prompt_name) | Often free text, may leak product internals. |
| Trace/span/thread IDs | Send the *count* / *category* / *target type*, not the UUID. UUIDs are low-entropy but join-able to backend tables — better to keep MCP analytics non-join-able to product data by default. |

These rules go in a `_redact_safe(...)` helper that the call-site builders use; nobody hand-builds a property dict.

---

## 5. Where the code lives

```
src/opik_mcp/
├── analytics/
│   ├── __init__.py        ← public surface: `track_event`, `get_analytics`
│   ├── client.py          ← async HTTP client, queue, fire-and-forget worker
│   ├── identity.py        ← install-id persistence, anonymous_id resolver
│   ├── events.py          ← event constants + property-builder helpers
│   └── wrappers.py        ← decorator-style tool-call timing wrapper
├── server.py              ← uses wrappers.instrument_tool(), one-line per tool
└── audit.py               ← write_auto_approval also calls track_event(...)
```

**Why a module, not a single file:** the surface has four concerns (transport, identity, taxonomy, instrumentation) and each is independently testable. The same shape mirrors `opik-backend/.../bi/`.

---

## 6. Files Changed (PR-by-PR)

This lands in **four small PRs**, each independently mergeable + reverteable.

- **PR-1 (transport + identity):** `src/opik_mcp/analytics/client.py`, `identity.py`, `events.py`, `__init__.py`, `src/opik_mcp/config.py` (new settings), `tests/test_analytics_client.py`, `tests/test_analytics_identity.py`, `tests/test_config.py` (analytics-settings additions), `README.md` (env var rows + opt-out section).
- **PR-2 (lifecycle + tool wrapper):** `src/opik_mcp/analytics/wrappers.py`, `src/opik_mcp/__main__.py` (emit `server_started`), `src/opik_mcp/server.py` (wrap every `@mcp.tool` + lazy first-call `session_initialized`), `tests/test_analytics_wrappers.py`, `tests/test_analytics_server_startup.py`.
- **PR-3 (ask_ollie + audit enrichment):** `src/opik_mcp/ask_ollie.py` (emit `ask_ollie_completed` with timing), `src/opik_mcp/audit.py` (emit `auto_approval`), `tests/test_ask_ollie_analytics.py`, `tests/test_audit_analytics.py`.
- **PR-4 (privacy assertion):** `tests/test_analytics_privacy.py` — drive every tool with realistic free-text inputs, capture every emitted analytics payload, assert no user-supplied substring appears in any event.

Each PR is gated by tests that prove (a) success path enqueues, (b) failure paths never raise, (c) opt-out drops events on the floor before any network attempt.

**Quality gates per PR (run before commit):**
```bash
uv run pytest                       # all green
uv run mypy src tests               # strict, no errors
uv run ruff check src tests         # clean
uv run ruff format --check src tests
```

---

## Task 1: Transport client + install-id + config

**Files:**
- New: `src/opik_mcp/analytics/__init__.py`, `client.py`, `identity.py`, `events.py`
- Modify: `src/opik_mcp/config.py`, `README.md`
- Test: `tests/test_analytics_client.py`, `tests/test_analytics_identity.py`, `tests/test_config.py` (additions)

### Public surface (defined upfront — every later task imports from here)

`src/opik_mcp/analytics/__init__.py` exports exactly:

```python
"""Public surface for opik-mcp analytics.

`get_analytics()` returns a process-wide singleton bound to the live `Settings`.
`track_event(event_type, properties)` is the convenience wrapper every call site
uses — never construct an `AnalyticsClient` directly outside this package.
"""
from __future__ import annotations

from functools import lru_cache

from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.analytics.events import (
    EVENT_ASK_OLLIE_COMPLETED,
    EVENT_AUTO_APPROVAL,
    EVENT_SERVER_STARTED,
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    bucket_count,
    bucket_text_len,
    bucket_tokens,
)
from opik_mcp.config import Settings, get_settings

__all__ = [
    "AnalyticsClient",
    "EVENT_ASK_OLLIE_COMPLETED",
    "EVENT_AUTO_APPROVAL",
    "EVENT_SERVER_STARTED",
    "EVENT_SESSION_INITIALIZED",
    "EVENT_TOOL_CALLED",
    "bucket_count",
    "bucket_text_len",
    "bucket_tokens",
    "get_analytics",
    "reset_analytics_for_tests",
    "track_event",
]


@lru_cache(maxsize=1)
def get_analytics() -> AnalyticsClient:
    return AnalyticsClient(get_settings())


def track_event(event_type: str, properties: dict[str, str]) -> None:
    """Convenience wrapper around the process-wide singleton."""
    get_analytics().track_event(event_type, properties)


def reset_analytics_for_tests() -> None:
    """Drop the singleton so the next `get_analytics()` rebuilds with fresh Settings.

    Call sites: pytest fixtures that override env vars and need a fresh client.
    Never call from production code.
    """
    get_analytics.cache_clear()
```

- [ ] **Step 1: Write failing tests for `Settings` additions**

Add to `tests/test_config.py` (the file exists; append):

```python
def test_analytics_enabled_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_ANALYTICS_ENABLED", raising=False)
    assert Settings().opik_mcp_analytics_enabled is True


def test_analytics_disable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_ANALYTICS_ENABLED", "false")
    assert Settings().opik_mcp_analytics_enabled is False


def test_analytics_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_ANALYTICS_URL", raising=False)
    assert Settings().opik_mcp_analytics_url == "https://stats.comet.com/notify/event/"


def test_analytics_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_ANALYTICS_ENVIRONMENT", raising=False)
    assert Settings().opik_mcp_analytics_environment == "prod"


def test_analytics_timeouts_have_sensible_defaults() -> None:
    s = Settings()
    assert s.opik_mcp_analytics_connect_timeout_s == 5.0
    assert s.opik_mcp_analytics_total_timeout_s == 10.0
```

Run: `uv run pytest tests/test_config.py -k analytics -v` — expect failures.

- [ ] **Step 2: Add fields to `Settings`** in `src/opik_mcp/config.py` per §2.4. Re-run tests; expect green.

- [ ] **Step 3: Write failing tests for `identity.py`**

`tests/test_analytics_identity.py`:

```python
import stat
from pathlib import Path
from uuid import UUID

import pytest

from opik_mcp.analytics import identity
from opik_mcp.config import Settings


@pytest.fixture(autouse=True)
def _fresh_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    identity._get_install_id.cache_clear()  # type: ignore[attr-defined]
    return tmp_path


def test_install_id_created_on_first_call(_fresh_home: Path) -> None:
    val = identity.get_install_id()
    UUID(val)  # raises if not a UUID
    path = _fresh_home / ".opik-mcp" / "install-id"
    assert path.read_text().strip() == val


def test_install_id_persists_across_cache_clear(_fresh_home: Path) -> None:
    first = identity.get_install_id()
    identity._get_install_id.cache_clear()  # type: ignore[attr-defined]
    assert identity.get_install_id() == first


def test_install_id_file_is_mode_0600(_fresh_home: Path) -> None:
    identity.get_install_id()
    path = _fresh_home / ".opik-mcp" / "install-id"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_file_is_regenerated(_fresh_home: Path) -> None:
    path = _fresh_home / ".opik-mcp" / "install-id"
    path.parent.mkdir(parents=True)
    path.write_text("not-a-uuid")
    val = identity.get_install_id()
    UUID(val)
    assert path.read_text().strip() == val


def test_resolve_anonymous_id_prefers_workspace(_fresh_home: Path) -> None:
    s = Settings(comet_workspace="ws-1")
    assert identity.resolve_anonymous_id(s) == "ws-1"


def test_resolve_anonymous_id_falls_back_to_install_id(_fresh_home: Path) -> None:
    s = Settings(comet_workspace=None)
    val = identity.resolve_anonymous_id(s)
    UUID(val)
```

Run: `uv run pytest tests/test_analytics_identity.py -v` — expect import failures.

- [ ] **Step 4: Implement `src/opik_mcp/analytics/identity.py`**

```python
"""Stable anonymous_id resolver — workspace if set, else persisted install UUID.

Mirrors `MetadataDAO.ANONYMOUS_ID` in opik-backend, file-backed instead of DB-backed.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from opik_mcp.config import Settings

logger = logging.getLogger("opik_mcp.analytics.identity")


def _install_id_path() -> Path:
    return Path.home() / ".opik-mcp" / "install-id"


@lru_cache(maxsize=1)
def _get_install_id() -> str:
    path = _install_id_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            raw = path.read_text().strip()
            return str(UUID(raw))
        except (ValueError, OSError):
            logger.warning("install-id file unreadable or malformed; regenerating")
    new_id = str(uuid4())
    path.write_text(new_id)
    try:
        path.chmod(0o600)
    except OSError:
        # On Windows / odd filesystems chmod may not apply — best-effort, not fatal.
        logger.debug("could not chmod install-id file", exc_info=True)
    return new_id


def get_install_id() -> str:
    return _get_install_id()


def resolve_anonymous_id(settings: Settings) -> str:
    return settings.comet_workspace or get_install_id()
```

Run tests; expect green.

- [ ] **Step 5: Write failing tests for `events.py`**

`tests/test_analytics_events.py`:

```python
from opik_mcp.analytics.events import bucket_count, bucket_text_len, bucket_tokens


def test_bucket_tokens_thresholds() -> None:
    assert bucket_tokens(0) == "<2k"
    assert bucket_tokens(1999) == "<2k"
    assert bucket_tokens(2000) == "2k-8k"
    assert bucket_tokens(7999) == "2k-8k"
    assert bucket_tokens(8000) == "8k-32k"
    assert bucket_tokens(31_999) == "8k-32k"
    assert bucket_tokens(32_000) == ">32k"
    assert bucket_tokens(10_000_000) == ">32k"


def test_bucket_text_len_thresholds() -> None:
    assert bucket_text_len("") == "<100"
    assert bucket_text_len("a" * 99) == "<100"
    assert bucket_text_len("a" * 100) == "100-1000"
    assert bucket_text_len("a" * 999) == "100-1000"
    assert bucket_text_len("a" * 1000) == ">1000"


def test_bucket_count_thresholds() -> None:
    assert bucket_count(0) == "0"
    assert bucket_count(1) == "1-10"
    assert bucket_count(10) == "1-10"
    assert bucket_count(11) == "11-100"
    assert bucket_count(100) == "11-100"
    assert bucket_count(101) == "101-1000"
    assert bucket_count(10_000) == ">1000"
```

- [ ] **Step 6: Implement `src/opik_mcp/analytics/events.py`**

```python
"""Event-name constants + low-cardinality bucket helpers.

Buckets are deliberate: they give actionable distributions without leaking
identifiable values. Thresholds picked to align with common LLM-context budgets
(~2k / ~8k / ~32k tokens) and to keep `tool_called` properties stringifiable.
"""
from __future__ import annotations

EVENT_SERVER_STARTED = "opik_mcp_server_started"
EVENT_SESSION_INITIALIZED = "opik_mcp_session_initialized"
EVENT_TOOL_CALLED = "opik_mcp_tool_called"
EVENT_ASK_OLLIE_COMPLETED = "opik_mcp_ask_ollie_completed"
EVENT_AUTO_APPROVAL = "opik_mcp_auto_approval"


def bucket_tokens(n: int) -> str:
    if n < 2_000:
        return "<2k"
    if n < 8_000:
        return "2k-8k"
    if n < 32_000:
        return "8k-32k"
    return ">32k"


def bucket_text_len(s: str | None) -> str:
    n = len(s) if s else 0
    if n < 100:
        return "<100"
    if n < 1000:
        return "100-1000"
    return ">1000"


def bucket_count(n: int) -> str:
    if n == 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 100:
        return "11-100"
    if n <= 1_000:
        return "101-1000"
    return ">1000"
```

Run `uv run pytest tests/test_analytics_events.py -v` — expect green.

- [ ] **Step 7: Write failing tests for `client.py`**

`tests/test_analytics_client.py`:

```python
import json
import threading
import time
from typing import Any

import httpx
import pytest
import respx

from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.config import Settings

URL = "https://stats.comet.com/notify/event/"


def _settings(**overrides: Any) -> Settings:
    base = dict(opik_mcp_analytics_enabled=True, comet_workspace="ws-1")
    return Settings(**{**base, **overrides})


def _drain(client: AnalyticsClient, deadline_s: float = 2.0) -> None:
    """Wait for the worker thread to finish dispatching everything in the queue."""
    client.flush(deadline_s=deadline_s)


@respx.mock
def test_track_event_posts_wire_shape() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"success": True}))
    client = AnalyticsClient(_settings())
    try:
        client.track_event("opik_mcp_test", {"foo": "bar"})
        _drain(client)
    finally:
        client.close()

    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["event_type"] == "opik_mcp_test"
    assert body["anonymous_id"] == "ws-1"
    props = body["event_properties"]
    assert props["foo"] == "bar"
    # Common properties stamped by the client:
    assert props["environment"] == "prod"
    assert props["workspace_id"] == "ws-1"
    assert "opik_mcp_version" in props
    assert "install_id" in props
    assert "python_version" in props
    assert "platform" in props
    assert "transport" in props


@respx.mock
def test_disabled_skips_post() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_mcp_analytics_enabled=False))
    try:
        client.track_event("opik_mcp_test", {})
        # Give the worker a moment in case it would erroneously fire.
        time.sleep(0.1)
    finally:
        client.close()
    assert not route.called


@respx.mock
def test_worker_swallows_exceptions() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    client = AnalyticsClient(_settings())
    try:
        # Must not raise.
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()


def test_queue_full_drops_silently() -> None:
    # Force a tiny queue so we can trigger overflow without 100 events.
    client = AnalyticsClient(_settings(), max_queue_size=2)
    # Block the worker so the queue actually fills.
    client._pause_worker_for_tests()
    try:
        for i in range(50):
            client.track_event("opik_mcp_test", {"i": str(i)})
        # No exception raised — overflow is silent.
    finally:
        client._resume_worker_for_tests()
        client.close()


@respx.mock
def test_track_event_safe_without_running_event_loop() -> None:
    """Caller in pure sync context (no asyncio loop) must work."""
    assert not _has_running_loop()
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings())
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()
    assert route.called


def _has_running_loop() -> bool:
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
```

- [ ] **Step 8: Implement `src/opik_mcp/analytics/client.py`**

```python
"""Fire-and-forget HTTP transport for opik-mcp analytics.

Daemon-thread worker model (not asyncio): callable from any context, including
`__main__.main()` before the MCP runtime has started a loop.
"""
from __future__ import annotations

import logging
import platform
import queue
import sys
import threading
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from opik_mcp.analytics.identity import get_install_id, resolve_anonymous_id
from opik_mcp.config import Settings

logger = logging.getLogger("opik_mcp.analytics")

_QUEUE_SENTINEL: Any = object()


def _opik_mcp_version() -> str:
    try:
        return version("opik-mcp")
    except PackageNotFoundError:
        return "unknown"


class AnalyticsClient:
    """Thread-safe, fire-and-forget event sender."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        max_queue_size: int = 100,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(
                connect=settings.opik_mcp_analytics_connect_timeout_s,
                read=settings.opik_mcp_analytics_total_timeout_s,
                write=settings.opik_mcp_analytics_total_timeout_s,
                pool=settings.opik_mcp_analytics_total_timeout_s,
            )
        )
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)
        self._worker: threading.Thread | None = None
        self._worker_started = threading.Event()
        self._test_pause = threading.Event()
        self._test_pause.set()  # not paused by default
        if self._settings.opik_mcp_analytics_enabled:
            self._start_worker()

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        if not self._settings.opik_mcp_analytics_enabled:
            return
        try:
            event = self._build_event(event_type, properties)
            self._queue.put_nowait(event)
        except queue.Full:
            logger.debug("analytics queue full; dropping event_type=%s", event_type)
        except Exception:
            # Last-resort guard — track_event MUST NEVER raise.
            logger.warning("analytics.track_event swallowed exception", exc_info=True)

    def flush(self, *, deadline_s: float = 2.0) -> None:
        """Block until the queue drains or deadline elapses. Test-only convenience."""
        self._queue.join() if deadline_s <= 0 else self._join_with_timeout(deadline_s)

    def _join_with_timeout(self, deadline_s: float) -> None:
        # queue.Queue has no timeout-join; spin briefly.
        import time
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if self._queue.unfinished_tasks == 0:
                return
            time.sleep(0.01)

    def close(self) -> None:
        if self._worker is None:
            self._http.close()
            return
        self._queue.put(_QUEUE_SENTINEL)
        self._worker.join(timeout=2.0)
        self._http.close()

    # ------ internals ------

    def _start_worker(self) -> None:
        if self._worker is not None:
            return
        t = threading.Thread(
            target=self._run_worker,
            name="opik-mcp-analytics",
            daemon=True,
        )
        self._worker = t
        t.start()
        self._worker_started.set()

    def _run_worker(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is _QUEUE_SENTINEL:
                    return
                self._test_pause.wait()  # no-op in production
                try:
                    self._http.post(self._settings.opik_mcp_analytics_url, json=event)
                except Exception:
                    logger.warning("analytics POST failed", exc_info=True)
            finally:
                self._queue.task_done()

    def _build_event(self, event_type: str, properties: dict[str, str]) -> dict[str, Any]:
        common: dict[str, str] = {
            "environment": self._settings.opik_mcp_analytics_environment,
            "opik_mcp_version": _opik_mcp_version(),
            "transport": self._settings.opik_mcp_transport,
            "install_id": get_install_id(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
        }
        if self._settings.comet_workspace:
            common["workspace_id"] = self._settings.comet_workspace
        common.update(properties)
        return {
            "anonymous_id": resolve_anonymous_id(self._settings),
            "event_type": event_type,
            "event_properties": common,
        }

    # ------ test hooks ------

    def _pause_worker_for_tests(self) -> None:
        self._test_pause.clear()

    def _resume_worker_for_tests(self) -> None:
        self._test_pause.set()
```

Run `uv run pytest tests/test_analytics_client.py -v` — expect green.

- [ ] **Step 9: Update README opt-out + env-var docs**

In `README.md`, add the new env-var rows to the configuration table (`OPIK_MCP_ANALYTICS_ENABLED`, `OPIK_MCP_ANALYTICS_URL`, `OPIK_MCP_ANALYTICS_ENVIRONMENT`, `OPIK_MCP_ANALYTICS_CONNECT_TIMEOUT_S`, `OPIK_MCP_ANALYTICS_TOTAL_TIMEOUT_S`) and add a top-level **Privacy & telemetry** section above the configuration block:

```markdown
### Privacy & telemetry

`opik-mcp` sends anonymous product-analytics events to `stats.comet.com` so the team can measure adoption and reliability. No tool input prose (queries, comments, scores, page context) is ever sent — only event type, timing buckets, and low-cardinality structural properties. See `docs/superpowers/plans/2026-05-15-mcp-analytics.md` §4.5 for the full "never sent" list.

To disable, set `OPIK_MCP_ANALYTICS_ENABLED=false`.
```

- [ ] **Step 10: Commit PR-1**

```bash
git add src/opik_mcp/analytics src/opik_mcp/config.py README.md \
        tests/test_config.py tests/test_analytics_identity.py \
        tests/test_analytics_events.py tests/test_analytics_client.py
git commit -m "feat(analytics): transport client, install-id, and config knobs (PR-1)"
```

---

## Task 2: Lifecycle events + tool-call wrapper

**Files:**
- New: `src/opik_mcp/analytics/wrappers.py`
- Modify: `src/opik_mcp/__main__.py`, `src/opik_mcp/server.py`
- Test: `tests/test_analytics_wrappers.py`, `tests/test_analytics_server_startup.py`

### Error-kind mapping (used by Step 3)

`instrument_tool` maps the existing typed exceptions in this repo to the closed enum from §4.3. New exception classes must be added to this table or fall through to `"unknown"`.

| Exception class | Source module | `error_kind` |
|---|---|---|
| `MissingConfigError` | `opik_mcp.config` | `"missing_config"` |
| `CometAuthError` | `opik_mcp.comet_client` | `"comet_auth_failed"` |
| `OllieNotEnabledError` | `opik_mcp.comet_client` | `"ollie_not_enabled"` |
| `CometProtocolError` | `opik_mcp.comet_client` | `"comet_protocol_error"` |
| `OpikAuthError` | `opik_mcp.opik_client` | `"opik_http_4xx"` |
| `OpikNotFoundError` | `opik_mcp.opik_client` | `"opik_http_4xx"` |
| `OpikValidationError` | `opik_mcp.opik_client` | `"opik_http_4xx"` |
| `OpikServerError` | `opik_mcp.opik_client` | `"opik_http_5xx"` |
| `PodNotReadyError` | `opik_mcp.ollie_client` | `"pod_warmup_timeout"` |
| `OllieAuthError` | `opik_mcp.ollie_client` | `"ollie_auth_failed"` |
| `OllieStreamError` | `opik_mcp.ollie_client` | `"ollie_stream_error"` |
| _anything else_ | — | `"unknown"` |

### Decision: emit `session_initialized` lazily, not from a SDK hook

`mcp.server.fastmcp.FastMCP` and `mcp.server.lowlevel.server.Server` do NOT expose a public `on_initialize` callback (verified). Building one would require subclassing `ServerSession` and overriding its private `_handle_incoming`, which is brittle and breaks across SDK updates.

Instead: emit `session_initialized` from inside the `instrument_tool` wrapper, the first time it sees a given `ctx.session` object. `ServerSession.client_params.clientInfo` is publicly available there and carries the same `name` / `version` / `protocolVersion` we need. Trade-off: the event fires on first *tool call*, not on connect. For Phase-1 analytics this is fine — sessions that connect but never call a tool aren't interesting signal.

- [ ] **Step 1: Write failing test for `server_started` emission**

`tests/test_analytics_server_startup.py`:

```python
from typing import Any

import pytest

from opik_mcp import __main__ as main_mod
from opik_mcp.analytics import EVENT_SERVER_STARTED


class _RecorderClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))

    def close(self) -> None:
        pass


def test_main_emits_server_started_then_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecorderClient()
    monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: recorder)

    run_calls: list[str] = []

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            run_calls.append(transport)

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    from opik_mcp.config import get_settings
    get_settings.cache_clear()

    main_mod.main()

    assert run_calls == ["stdio"]
    event_types = [e[0] for e in recorder.events]
    assert EVENT_SERVER_STARTED in event_types
    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["transport"] == "stdio"
    assert started["analytics_enabled"] == "true"
    assert started["has_workspace"] in {"true", "false"}
    assert started["has_api_key"] in {"true", "false"}
    assert started["has_default_project"] in {"true", "false"}
```

Run; expect failure.

- [ ] **Step 2: Emit `opik_mcp_server_started` from `__main__.main()`**

In `src/opik_mcp/__main__.py`, after `_configure_logging(...)` and before transport dispatch:

```python
from opik_mcp.analytics import EVENT_SERVER_STARTED, track_event

track_event(EVENT_SERVER_STARTED, {
    "transport": transport,
    "analytics_enabled": str(settings.opik_mcp_analytics_enabled).lower(),
    "has_workspace": str(settings.comet_workspace is not None).lower(),
    "has_api_key": str(settings.opik_api_key is not None).lower(),
    "has_default_project": str(settings.opik_default_project_name is not None).lower(),
})
```

Run test; expect green.

- [ ] **Step 3: Write failing tests for `instrument_tool`**

`tests/test_analytics_wrappers.py`:

```python
from typing import Any

import pytest

from opik_mcp.analytics import EVENT_TOOL_CALLED
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.comet_client import CometAuthError, OllieNotEnabledError
from opik_mcp.opik_client import OpikAuthError, OpikServerError
from opik_mcp.ollie_client import OllieStreamError, PodNotReadyError


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    return r


async def test_success_emits_tool_called(recorder: _Recorder) -> None:
    @instrument_tool("hello")
    async def fn() -> str:
        return "hi"

    assert await fn() == "hi"
    assert len(recorder.events) == 1
    et, props = recorder.events[0]
    assert et == EVENT_TOOL_CALLED
    assert props["tool_name"] == "hello"
    assert props["success"] == "true"
    assert "error_kind" not in props
    assert int(props["duration_ms"]) >= 0


@pytest.mark.parametrize(
    "exc, expected_kind",
    [
        (CometAuthError("x"), "comet_auth_failed"),
        (OllieNotEnabledError("x"), "ollie_not_enabled"),
        (OpikAuthError("x"), "opik_http_4xx"),
        (OpikServerError("x"), "opik_http_5xx"),
        (PodNotReadyError("x"), "pod_warmup_timeout"),
        (OllieStreamError("x"), "ollie_stream_error"),
        (ValueError("x"), "unknown"),
    ],
)
async def test_error_kind_mapping(
    recorder: _Recorder, exc: Exception, expected_kind: str
) -> None:
    @instrument_tool("read")
    async def fn() -> str:
        raise exc

    with pytest.raises(type(exc)):
        await fn()
    et, props = recorder.events[0]
    assert props["success"] == "false"
    assert props["error_kind"] == expected_kind


async def test_props_fn_merges_extras(recorder: _Recorder) -> None:
    def props_fn(result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
        return {"entity_type": kwargs.get("entity_type", "")}

    @instrument_tool("read", props_fn=props_fn)
    async def fn(*, entity_type: str) -> str:
        return "ok"

    await fn(entity_type="trace")
    _, props = recorder.events[0]
    assert props["entity_type"] == "trace"
```

- [ ] **Step 4: Implement `src/opik_mcp/analytics/wrappers.py`**

```python
"""Decorator that emits `opik_mcp_tool_called` on every wrapped tool invocation.

Also lazily emits `opik_mcp_session_initialized` the first time it sees a given
`ctx.session` — Phase-1 substitute for a real `initialize` SDK hook.
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from weakref import WeakSet

from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    get_analytics,
)
from opik_mcp.comet_client import (
    CometAuthError,
    CometProtocolError,
    OllieNotEnabledError,
)
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)

logger = logging.getLogger("opik_mcp.analytics.wrappers")

T = TypeVar("T")

_ERROR_KIND_TABLE: tuple[tuple[type[BaseException], str], ...] = (
    (MissingConfigError, "missing_config"),
    (CometAuthError, "comet_auth_failed"),
    (OllieNotEnabledError, "ollie_not_enabled"),
    (CometProtocolError, "comet_protocol_error"),
    (OpikAuthError, "opik_http_4xx"),
    (OpikNotFoundError, "opik_http_4xx"),
    (OpikValidationError, "opik_http_4xx"),
    (OpikServerError, "opik_http_5xx"),
    (PodNotReadyError, "pod_warmup_timeout"),
    (OllieAuthError, "ollie_auth_failed"),
    (OllieStreamError, "ollie_stream_error"),
)


def _classify(exc: BaseException) -> str:
    for cls, kind in _ERROR_KIND_TABLE:
        if isinstance(exc, cls):
            return kind
    return "unknown"


# Indirection so tests can patch the singleton.
def _client() -> Any:
    return get_analytics()


# Per-process set of session ids we've already announced. WeakSet so dead
# sessions get garbage-collected and don't leak memory across long uptime.
_seen_sessions: WeakSet[Any] = WeakSet()


def _maybe_emit_session_initialized(kwargs: dict[str, Any]) -> None:
    ctx = kwargs.get("ctx")
    if ctx is None:
        return
    session = getattr(ctx, "session", None)
    if session is None or session in _seen_sessions:
        return
    _seen_sessions.add(session)
    params = getattr(session, "client_params", None)
    client_info = getattr(params, "clientInfo", None) if params is not None else None
    props: dict[str, str] = {
        "mcp_host": getattr(client_info, "name", "") or "",
        "mcp_client_version": getattr(client_info, "version", "") or "",
        "mcp_protocol_version": getattr(params, "protocolVersion", "") or "" if params else "",
    }
    try:
        _client().track_event(EVENT_SESSION_INITIALIZED, props)
    except Exception:
        logger.debug("session_initialized emit failed", exc_info=True)


PropsFn = Callable[[Any, dict[str, Any]], dict[str, str]]


def instrument_tool(
    name: str,
    *,
    props_fn: PropsFn | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Wrap an async MCP tool handler so every call emits `opik_mcp_tool_called`."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            _maybe_emit_session_initialized(kwargs)
            t0 = time.monotonic()
            error_kind: str | None = None
            result: T | None = None
            try:
                result = await fn(*args, **kwargs)
                return result
            except BaseException as exc:
                if isinstance(exc, Exception):
                    error_kind = _classify(exc)
                raise
            finally:
                props: dict[str, str] = {
                    "tool_name": name,
                    "success": "false" if error_kind else "true",
                    "duration_ms": str(int((time.monotonic() - t0) * 1000)),
                }
                if error_kind:
                    props["error_kind"] = error_kind
                if props_fn is not None and error_kind is None:
                    try:
                        props.update(props_fn(result, kwargs))
                    except Exception:
                        logger.debug("props_fn raised; skipping extras", exc_info=True)
                try:
                    _client().track_event(EVENT_TOOL_CALLED, props)
                except Exception:
                    logger.debug("tool_called emit failed", exc_info=True)
        return wrapper
    return decorator
```

Run `uv run pytest tests/test_analytics_wrappers.py -v` — expect green.

- [ ] **Step 5: Apply wrapper + per-tool `props_fn` helpers in `server.py`**

Add per-tool extras builders at the top of `server.py` (after imports, before `mcp = FastMCP(...)`):

```python
from opik_mcp.analytics.events import bucket_count, bucket_text_len, bucket_tokens
from opik_mcp.analytics.wrappers import instrument_tool


def _hello_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {"name_was_default": str(kwargs.get("name", "world") == "world").lower()}


def _read_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    raw_id = kwargs.get("id", "")
    id_kind = "uri" if raw_id.startswith("opik://") else ("uuid" if _looks_like_uuid(raw_id) else "name")
    return {
        "entity_type": kwargs.get("entity_type", ""),
        "id_kind": id_kind,
    }


def _list_props(result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {
        "entity_type": kwargs.get("entity_type", ""),
        "had_name_filter": str(kwargs.get("name") is not None).lower(),
        "page": str(kwargs.get("page", 1)),
        "size": str(kwargs.get("size", 25)),
    }


def _score_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    target = kwargs.get("target")
    target_type = getattr(target, "type", "") if target is not None else ""
    name = kwargs.get("name", "")
    canonical = {"helpfulness", "hallucination", "tone"}
    return {
        "target_type": target_type,
        "score_name_bucket": name if name in canonical else "other",
        "has_reason": str(kwargs.get("reason") is not None).lower(),
        "has_category": str(kwargs.get("category_name") is not None).lower(),
    }


def _comment_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    target = kwargs.get("target")
    return {
        "target_type": getattr(target, "type", "") if target is not None else "",
        "text_length_bucket": bucket_text_len(kwargs.get("text", "")),
    }


def _ask_ollie_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {
        "had_continuation": str(kwargs.get("thread_id") is not None).lower(),
        "had_page_context": str(kwargs.get("page_context") is not None).lower(),
        "had_project_name": str(kwargs.get("project_name") is not None).lower(),
        "attach_resources_count": bucket_count(len(kwargs.get("attach_resources") or [])),
    }


def _looks_like_uuid(s: str) -> bool:
    from uuid import UUID
    try:
        UUID(s)
        return True
    except (ValueError, TypeError):
        return False
```

Then add `@instrument_tool(...)` between each `@mcp.tool(...)` and the `async def`:

```python
@mcp.tool()
@instrument_tool("hello", props_fn=_hello_props)
async def hello(...): ...

@mcp.tool()
@instrument_tool("read", props_fn=_read_props)
async def read(...): ...

@mcp.tool(name="list")
@instrument_tool("list", props_fn=_list_props)
async def list_entities(...): ...

@mcp.tool()
@instrument_tool("ask_ollie", props_fn=_ask_ollie_props)
async def ask_ollie(...): ...

@mcp.tool()
@instrument_tool("score", props_fn=_score_props)
async def score(...): ...

@mcp.tool()
@instrument_tool("comment", props_fn=_comment_props)
async def comment(...): ...
```

Decorator order matters: `@mcp.tool()` must be **outermost** so FastMCP sees the (already-wrapped) callable. The wrapper preserves `__wrapped__` via `functools.wraps`, so FastMCP's schema inference still finds the original signature.

- [ ] **Step 6: Integration test — drive tools through `mcp.shared.memory`**

`tests/test_analytics_wrappers_integration.py`:

```python
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.analytics import EVENT_TOOL_CALLED, reset_analytics_for_tests
from opik_mcp.server import mcp


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch):
    events: list[tuple[str, dict[str, str]]] = []

    class _R:
        def track_event(self, et: str, props: dict[str, str]) -> None:
            events.append((et, props))

    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: _R())
    yield events


async def test_hello_emits_tool_called(recorder: list) -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:  # noqa: SLF001
        await client.call_tool("hello", {"name": "world"})

    tool_events = [(et, p) for et, p in recorder if et == EVENT_TOOL_CALLED]
    assert len(tool_events) == 1
    _, props = tool_events[0]
    assert props["tool_name"] == "hello"
    assert props["success"] == "true"
    assert props["name_was_default"] == "true"
```

- [ ] **Step 7: Commit PR-2**

```bash
git add src/opik_mcp/analytics/wrappers.py src/opik_mcp/__main__.py \
        src/opik_mcp/server.py tests/test_analytics_wrappers.py \
        tests/test_analytics_server_startup.py \
        tests/test_analytics_wrappers_integration.py
git commit -m "feat(analytics): server_started, session_initialized, and per-tool wrapper (PR-2)"
```

---

## Task 3: `ask_ollie` and audit enrichment

**Files:**
- Modify: `src/opik_mcp/ask_ollie.py`, `src/opik_mcp/audit.py`
- Test: `tests/test_ask_ollie_analytics.py`, `tests/test_audit_analytics.py`

### Timing capture must survive errors

The original draft captured `pod_warmup_ms` and `t0` outside any error path — if `wait_ready` raised, the `ask_ollie_completed` event never fired. Fix: stamp `t0` at function entry and wrap warmup/stream in a single `try/finally` that always emits the event, with `completion_state` reflecting the actual outcome.

- [ ] **Step 1: Write failing tests for `ask_ollie_completed`**

`tests/test_ask_ollie_analytics.py`:

```python
from typing import Any
from unittest.mock import AsyncMock

import pytest

from opik_mcp.analytics import EVENT_ASK_OLLIE_COMPLETED
from opik_mcp.ask_ollie import run_ask_ollie
from opik_mcp.comet_client import PodDiscovery
from opik_mcp.config import Settings
from opik_mcp.ollie_client import OllieStreamError, PodNotReadyError


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.ask_ollie._analytics", lambda: r)
    return r


def _settings() -> Settings:
    return Settings(opik_api_key="k", comet_workspace="ws-1")


async def _run_with(comet: Any, ollie: Any, recorder: _Recorder, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "query": "hi",
        "settings": _settings(),
        "comet_client": comet,
        "ollie_client": ollie,
    }
    kwargs.update(overrides)
    return await run_ask_ollie(**kwargs)


async def test_message_end_emits_completed_success(recorder: _Recorder) -> None:
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.return_value = "sess-1"

    async def _stream(*_a: Any, **_kw: Any):
        from opik_mcp.ollie_client import SSEEvent
        yield SSEEvent(event="message_delta", data={"payload": {"delta": "hello"}})
        yield SSEEvent(event="message_end", data={"payload": {}})

    ollie.stream_events = _stream

    await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    assert p["success"] == "true"
    assert p["completion_state"] == "message_end"
    assert int(p["pod_warmup_ms"]) >= 0
    assert int(p["total_duration_ms"]) >= 0
    assert int(p["event_count"]) == 2


async def test_pod_warmup_failure_emits_completed_error(recorder: _Recorder) -> None:
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.wait_ready.side_effect = PodNotReadyError("boom")

    with pytest.raises(PodNotReadyError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    assert completed[0]["completion_state"] == "error"
    assert completed[0]["success"] == "false"


async def test_message_cancelled_state(recorder: _Recorder) -> None:
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.return_value = "sess-1"

    async def _stream(*_a: Any, **_kw: Any):
        from opik_mcp.ollie_client import SSEEvent
        yield SSEEvent(event="message_cancelled", data={"payload": {}})

    ollie.stream_events = _stream
    await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert completed[0]["completion_state"] == "cancelled"
    assert completed[0]["success"] == "true"
```

- [ ] **Step 2: Instrument `run_ask_ollie`**

In `src/opik_mcp/ask_ollie.py`, add at module level (below the existing imports):

```python
from opik_mcp.analytics import EVENT_ASK_OLLIE_COMPLETED


def _analytics() -> Any:
    # Lazy attribute fetch so tests can monkeypatch at module scope.
    from opik_mcp.analytics import get_analytics
    return get_analytics()


def _completion_state(*, saw_message_end: bool, cancelled: bool, errored: bool) -> str:
    if errored:
        return "error"
    if cancelled:
        return "cancelled"
    if saw_message_end:
        return "message_end"
    return "truncated"
```

Then rewrite the body of `run_ask_ollie` to capture timing in a `try/finally`. The minimal-diff approach:

```python
async def run_ask_ollie(...) -> AskOllieResult:
    t0 = time.monotonic()
    pod_warmup_ms = 0
    first_event_at: float | None = None
    events_seen = 0
    auto_approval_targets: list[str] = []
    errored = False
    saw_message_end = False
    cancelled = False

    try:
        settings = settings or get_settings()
        api_key, workspace = require_ollie_config(settings)
        # ... (existing code unchanged up to ollie.wait_ready) ...

        warmup_start = time.monotonic()
        await ollie.wait_ready(discovery.compute_url, discovery.ppauth, on_tick=on_tick)
        pod_warmup_ms = int((time.monotonic() - warmup_start) * 1000)

        # ... (existing create_session + stream setup) ...

        # Inside the `async for sse in ollie.stream_events(...)` loop, BEFORE the
        # existing `progress_counter += 1`:
        events_seen += 1
        if first_event_at is None:
            first_event_at = time.monotonic()

        # When auto-approving in the `confirm_required` branch, after the audit
        # write succeeds:
        if target_tool:
            auto_approval_targets.append(target_tool)

        # ... (rest of loop unchanged) ...

        return AskOllieResult(...)
    except BaseException:
        errored = True
        raise
    finally:
        ttfe_ms = (
            int((first_event_at - t0) * 1000) if first_event_at is not None else ""
        )
        try:
            _analytics().track_event(EVENT_ASK_OLLIE_COMPLETED, {
                "success": "false" if errored else "true",
                "total_duration_ms": str(int((time.monotonic() - t0) * 1000)),
                "pod_warmup_ms": str(pod_warmup_ms),
                "time_to_first_event_ms": str(ttfe_ms),
                "event_count": str(events_seen),
                "had_continuation": str(thread_id is not None).lower(),
                "had_page_context": str(page_context is not None).lower(),
                "had_project_name": str(project_name is not None).lower(),
                "attach_resources_count": str(len(attach_resources or [])),
                "completion_state": _completion_state(
                    saw_message_end=saw_message_end,
                    cancelled=cancelled,
                    errored=errored,
                ),
                "auto_approvals_count": str(len(auto_approval_targets)),
                "auto_approval_tools": ",".join(sorted(set(auto_approval_targets))),
            })
        except Exception:
            logger.debug("ask_ollie_completed emit failed", exc_info=True)
```

NOTE: rename the existing local `events_seen` use sites so the counter we increment is the same one referenced in analytics. If `events_seen` already exists in the function (`run_ask_ollie` body), reuse it instead of redeclaring.

Run `uv run pytest tests/test_ask_ollie_analytics.py -v` — expect green.

- [ ] **Step 3: Write failing test for `auto_approval` audit emission**

`tests/test_audit_analytics.py`:

```python
import pytest

from opik_mcp.analytics import EVENT_AUTO_APPROVAL
from opik_mcp.audit import write_auto_approval


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


def test_write_auto_approval_emits_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="add_score",
        summary="add a score",
        input={"value": 0.9},
    )

    events = [(et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL]
    assert len(events) == 1
    _, props = events[0]
    assert props["tool"] == "ask_ollie"
    assert props["target_tool"] == "add_score"
    assert props["had_summary"] == "true"


def test_write_auto_approval_missing_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="add_score",
        summary=None,
        input={},
    )
    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["had_summary"] == "false"
```

- [ ] **Step 4: Emit `auto_approval` from `audit.write_auto_approval`**

In `src/opik_mcp/audit.py`, add at module scope:

```python
def _analytics_for_audit() -> Any:
    # Lazy import — audit.py is imported by ask_ollie at import time and
    # analytics depends on Settings; keep this circular-safe.
    from opik_mcp.analytics import get_analytics
    return get_analytics()
```

And at the end of `write_auto_approval`, after `_audit_logger.info(...)`:

```python
    try:
        _analytics_for_audit().track_event("opik_mcp_auto_approval", {
            "tool": "ask_ollie",
            "target_tool": target_tool or "",
            "had_summary": str(summary is not None).lower(),
        })
    except Exception:
        # Audit row is the source of truth; analytics is a secondary signal.
        # Never let analytics fail the auto-approval write.
        logger = logging.getLogger("opik_mcp.audit")
        logger.debug("auto_approval analytics emit failed", exc_info=True)

    return row
```

Run `uv run pytest tests/test_audit_analytics.py -v` — expect green. Also re-run `tests/test_audit.py` — must still pass.

- [ ] **Step 5: Commit PR-3**

```bash
git add src/opik_mcp/ask_ollie.py src/opik_mcp/audit.py \
        tests/test_ask_ollie_analytics.py tests/test_audit_analytics.py
git commit -m "feat(analytics): ask_ollie_completed and auto_approval events (PR-3)"
```

---

## Task 4: Privacy assertion test (PR-4)

**Files:**
- New: `tests/test_analytics_privacy.py`

This test is the mechanical enforcement of §4.5 — non-negotiable per the testing strategy in §7.

- [ ] **Step 1: Author `tests/test_analytics_privacy.py`**

```python
"""Mechanically enforce §4.5 — no user prose ever appears in an analytics event.

Drives every MCP tool with realistic free-text inputs designed to be uniquely
identifiable (UUID-ish strings, distinctive phrases), then walks every emitted
event payload and asserts none of those substrings show up.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.comet_client import PodDiscovery
from opik_mcp.server import mcp
from opik_mcp.score_comment import Target


# Substrings that must NEVER appear in any analytics event. Each one is a
# realistic free-text payload a user might pass, chosen to be globally unique
# inside the test process so even a partial leak would trigger.
FORBIDDEN = [
    "Why-did-trace-7c4a-fail-on-prod-PRIVATE-QUERY",
    "https://internal.example.com/super-secret-page",
    "RegressionVsYesterday-INTERNAL-COMMENT-TOKEN",
    "BadOutputReason-SHOULD-NEVER-APPEAR-IN-TELEMETRY",
    "ProjectNameMustNotLeak-XYZ-001",
    "AttachedTraceURI-opik://traces/UNIQUE-LEAK-CANARY",
]


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch):
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    monkeypatch.setattr("opik_mcp.ask_ollie._analytics", lambda: r)
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: r)
    return r


def _assert_no_leak(events: list[tuple[str, dict[str, str]]]) -> None:
    payload = json.dumps(events)
    for forbidden in FORBIDDEN:
        assert forbidden not in payload, (
            f"PRIVACY BREACH: {forbidden!r} leaked into analytics payload"
        )


async def test_ask_ollie_strips_all_user_text(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    from opik_mcp.ask_ollie import run_ask_ollie
    from opik_mcp.ollie_client import SSEEvent

    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.return_value = "sess-1"

    async def _stream(*_a: Any, **_kw: Any):
        yield SSEEvent(event="message_end", data={"payload": {}})

    ollie.stream_events = _stream

    from opik_mcp.config import Settings
    await run_ask_ollie(
        query=FORBIDDEN[0],
        page_context=FORBIDDEN[1],
        project_name=FORBIDDEN[4],
        attach_resources=[FORBIDDEN[5]],
        settings=Settings(opik_api_key="k", comet_workspace="ws-1"),
        comet_client=comet,
        ollie_client=ollie,
    )
    _assert_no_leak(recorder.events)


async def test_score_strips_reason(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    from opik_mcp.score_comment import run_score

    class _StubClient:
        async def add_trace_feedback_score(self, *_a: Any, **_kw: Any) -> None: ...
        async def add_span_feedback_score(self, *_a: Any, **_kw: Any) -> None: ...
        async def add_thread_feedback_score(self, *_a: Any, **_kw: Any) -> None: ...

    await run_score(
        target=Target(type="trace", id="00000000-0000-0000-0000-000000000001"),
        name="helpfulness",
        value=0.9,
        reason=FORBIDDEN[3],
        client=_StubClient(),
    )
    _assert_no_leak(recorder.events)


async def test_comment_strips_text(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    from opik_mcp.score_comment import run_comment

    class _StubClient:
        async def add_trace_comment(self, *_a: Any, **_kw: Any) -> None: ...
        async def add_span_comment(self, *_a: Any, **_kw: Any) -> None: ...
        async def add_thread_comment(self, *_a: Any, **_kw: Any) -> None: ...

    await run_comment(
        target=Target(type="trace", id="00000000-0000-0000-0000-000000000001"),
        text=FORBIDDEN[2],
        client=_StubClient(),
    )
    _assert_no_leak(recorder.events)
```

- [ ] **Step 2: Commit PR-4**

```bash
git add tests/test_analytics_privacy.py
git commit -m "test(analytics): mechanical privacy assertion across all tools (PR-4)"
```

---

## 7. Testing strategy

| Layer | What it proves |
|---|---|
| Unit (client) | Wire format, opt-out, fire-and-forget, queue overflow drop, common-property stamping. |
| Unit (identity) | Install-id creation, persistence, corruption recovery, workspace > install-id precedence. |
| Unit (wrapper) | Each error class maps to the right `error_kind`; success path returns unchanged. |
| Integration (server) | Stand up the FastMCP server with `mcp.shared.memory`, call each tool, assert one `opik_mcp_tool_called` event with expected props. |
| Integration (ask_ollie) | Use the existing `respx`-driven Comet + Ollie stubs from `tests/test_*ask_ollie*.py`; assert `ask_ollie_completed` shape under message_end / cancelled / error / truncated. |
| Privacy assertion | A dedicated test in `tests/test_analytics_privacy.py` runs every tool with realistic user-string inputs and grep-asserts that none of those substrings appear in any captured event payload. |

The privacy assertion is non-negotiable — it's how we prove the "what we never send" promise is mechanically enforced.

---

## 8. Rollout

1. Land PR-1, PR-2, PR-3 in order. Each PR includes its own privacy test that re-runs against the new surface.
2. Cut a release candidate. Run `uvx --from <built wheel> opik-mcp` locally, sniff the traffic to `stats.comet.com` (`mitmproxy`), confirm shape matches a known-good `opik-backend` event side-by-side.
3. **PR a dashboard** in Comet's internal Metabase (out of repo) keyed off `event_type LIKE 'opik_mcp_%'`: install count, sessions/day by host, tool calls by `tool_name × success`, `ask_ollie` completion-state distribution, top error_kinds. This is the artifact the Phase-2 decision uses.
4. Beta to internal users for 1 week. Confirm events arrive, properties are clean, privacy assertion holds.
5. Public release. README highlights the opt-out env var above the fold.

---

## 9. Open questions

1. **Should `install_id` be reset-able from the CLI?** (`opik-mcp reset-id`) — Recommend yes, lightweight escape hatch for users with strong privacy preferences. Cheap to add in PR-1.
2. **Do we want a single `opik_mcp_session_terminated` event with aggregate counts (calls per tool, total duration)?** Useful for retention math but adds a not-always-fired event (the host can hard-kill the process). Recommend defer — we can derive session length from `tool_called` timestamps.
3. **Should `event_type` use the legacy `opik_` prefix (matching backend) or the proposed `opik_mcp_` prefix?** Recommend `opik_mcp_` — the surfaces are different enough (MCP host-driven vs API/UI user-driven) that mixing them in dashboards causes confusion. Backend's `opik_*` events keep their names; we don't rewrite history.
4. **`OPIK_USAGE_REPORT_URL` vs `OPIK_MCP_ANALYTICS_URL` as the env-var name** — separate name because of separate enable-flag (§2.4 rationale).

---

## 10. References

- Java AnalyticsService contract: `apps/opik-backend/src/main/java/com/comet/opik/infrastructure/bi/AnalyticsService.java`
- Java StatsClient transport: `apps/opik-backend/src/main/java/com/comet/opik/infrastructure/bi/StatsClient.java`
- Java BiEvent wire format: `apps/opik-backend/src/main/java/com/comet/opik/infrastructure/bi/BiEvent.java`
- Frontend tracking: `apps/opik-frontend/src/lib/analytics/tracking.ts`
- Backend config knobs: `apps/opik-backend/config.yml` (lines 461–481: `analytics:` + `usageReport:`)
- Existing event catalog (Java call sites): grep `analyticsService.trackEvent(` in `apps/opik-backend/src/main/`
- Phase-1 scope: `docs/phase-1.md`, `docs/team-brief.md`
- ADR 0005 (YOLO mode audit log this hooks into): `docs/decisions/0005-ask-ollie-yolo-mode.md`
- ADR 0006 (deferred admin UI — explains why MCP analytics doesn't surface to customers yet): `docs/decisions/0006-admin-dashboard-deferred.md`

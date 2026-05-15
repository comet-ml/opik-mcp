# Project Scoping Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the project-scoping gaps surfaced in the user-flow analysis so a freshly-connected user gets correct project routing with minimal LLM round-trips.

**Architecture:** All project hints flow through one seam — the MCP `InitializeResult.instructions` blob (rendered once per session by `instructions.py`). Tools remain stateless. The LLM reads the default project from instructions, threads it through every tool call. `score` becomes consistent with `ask_ollie` by accepting `project_id` and resolving to `project_name` server-side when needed (the backend's thread-feedback endpoint only takes name). A live test pins down Ollie's actual behavior when `project_id` is None, answering the open question.

**Tech Stack:** Python 3.13, pydantic-settings, FastMCP, pytest+anyio.

---

## Files Changed

- `src/opik_mcp/config.py` — add `opik_default_project_id` / `opik_default_project_name`.
- `src/opik_mcp/instructions.py` — inject default project clause into rendered blob.
- `src/opik_mcp/score_comment.py` — accept `project_id` on `run_score`, resolve to name when backend requires name.
- `src/opik_mcp/server.py` — expose `project_id` on the `score` tool.
- `src/opik_mcp/opik_client.py` — no signature change; `get_project` already exists.
- `tests/test_config.py` — assert new fields parse from env.
- `tests/test_instructions.py` — assert default-project clause appears / is omitted.
- `tests/test_score_comment.py` — assert id→name resolution for thread scoring.
- `tests/test_ask_ollie_live.py` — new live test for `project_id=None` behavior.
- `README.md` — new env var rows in the configuration table.
- `docs/design.md` — new "Project scoping" subsection.

---

## Task 1: Add default-project settings

**Files:**
- Modify: `src/opik_mcp/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_config.py`:

```python
def test_default_project_id_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_DEFAULT_PROJECT_ID", "11111111-2222-3333-4444-555555555555")
    s = Settings()
    assert s.opik_default_project_id == "11111111-2222-3333-4444-555555555555"


def test_default_project_name_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_DEFAULT_PROJECT_NAME", "chatbot-prod")
    s = Settings()
    assert s.opik_default_project_name == "chatbot-prod"


def test_default_project_defaults_to_none() -> None:
    s = Settings()
    assert s.opik_default_project_id is None
    assert s.opik_default_project_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v -k default_project`
Expected: 3 failures — `AttributeError: 'Settings' object has no attribute 'opik_default_project_id'`.

- [ ] **Step 3: Add fields to Settings**

In `src/opik_mcp/config.py`, after `opik_url: str | None = None`, add:

```python
    # Default project hint surfaced to the LLM via the instructions blob.
    # Tools remain stateless — the LLM is responsible for passing project on
    # each call. Unset = no hint, LLM must discover or ask.
    opik_default_project_id: str | None = None
    opik_default_project_name: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v -k default_project`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add src/opik_mcp/config.py tests/test_config.py
git commit -m "feat: add OPIK_DEFAULT_PROJECT_ID/NAME settings

Surface the user's default project so it can be injected into the
instructions blob. Tools remain stateless; this is a hint for the LLM.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Inject default project into instructions blob

**Files:**
- Modify: `src/opik_mcp/instructions.py`
- Test: `tests/test_instructions.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_instructions.py`:

```python
def test_render_includes_default_project_when_id_set() -> None:
    s = _settings(opik_default_project_id="proj-uuid-123")
    out = render_instructions(s)
    assert "proj-uuid-123" in out
    assert "default project" in out.lower()


def test_render_includes_default_project_name_when_only_name_set() -> None:
    s = _settings(opik_default_project_name="chatbot-prod")
    out = render_instructions(s)
    assert "chatbot-prod" in out


def test_render_includes_both_id_and_name_when_both_set() -> None:
    s = _settings(
        opik_default_project_id="proj-uuid-123",
        opik_default_project_name="chatbot-prod",
    )
    out = render_instructions(s)
    assert "proj-uuid-123" in out
    assert "chatbot-prod" in out


def test_render_omits_default_project_when_unset() -> None:
    out = render_instructions(_settings())
    assert "default project" not in out.lower()
```

Confirm `_settings` helper in that file already accepts kwargs that go to `Settings(**kwargs)`. If it doesn't, look at the existing fixture and extend it. The existing tests pass workspace and url override via `_settings(...)`, so the helper already takes kwargs.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_instructions.py -v -k default_project`
Expected: 4 failures.

- [ ] **Step 3: Update template and render function**

In `src/opik_mcp/instructions.py`, replace the `_TEMPLATE` constant:

```python
_TEMPLATE = """\
You're connected to Opik (Comet's LLM observability platform){user_clause} \
in workspace "{workspace}". The Opik UI is at {opik_url}.
{default_project_clause}\
Tool selection:
- read / list: use for any "show me X" or "what is Y" — these are the cheapest \
reads. read takes (entity_type, id_or_name_or_uri); list takes (entity_type, \
optional name filter, page, size). Readable entity types include trace, span, \
project, experiment, prompt, test_suite. Composite reads (trace, prompt) inline \
their child collections so one call usually gets the full picture.
- Direct writes (score, comment): use when the user's intent is concrete and \
well-defined ("score this trace 0.8 on helpfulness", "comment 'retry with \
temperature=0' on span X"). Skip ask_ollie for these — narrower tools are \
faster and more deterministic.
- ask_ollie: use for investigative questions ("why is X failing?"), cross-entity \
synthesis ("compare experiments A and B"), or when authoring / instrumentation \
requires Opik domain expertise. Returns a thread_id you can pass back for \
follow-ups.

Today's date is {date}.\
"""
```

Then replace the body of `render_instructions` to add the new clause builder. The full function:

```python
def render_instructions(
    settings: Settings | None = None,
    *,
    user_email: str | None = None,
    today: datetime | None = None,
) -> str:
    """Render the instructions blob for the current session.

    ``user_email`` is omitted from the rendered text when unknown — better
    no claim than a stale claim. Same for ``opik_url`` — falls back to a
    generic placeholder if the config is partial.
    """
    s = settings if settings is not None else get_settings()
    workspace = s.comet_workspace or "(workspace not configured)"
    if s.opik_url:
        opik_url = s.opik_url.rstrip("/")
    elif s.comet_url_override:
        opik_url = f"{s.comet_url_override.rstrip('/')}/opik"
    else:
        opik_url = "(Opik URL not configured)"

    user_clause = f" as {user_email}" if user_email else ""
    today = today if today is not None else datetime.now(UTC)
    date = today.strftime("%Y-%m-%d")

    default_project_clause = _render_default_project_clause(s)

    return _TEMPLATE.format(
        user_clause=user_clause,
        workspace=workspace,
        opik_url=opik_url,
        default_project_clause=default_project_clause,
        date=date,
    )


def _render_default_project_clause(s: Settings) -> str:
    """Return a paragraph naming the default project, or '' if none configured.

    When configured, the LLM sees the hint once per session and is told to
    pass it as project_id/project_name on every tool call unless the user
    mentions a different project. Both id and name shown when both are set so
    the LLM can choose the unambiguous form (id) but still surface the name
    in prose for the user.
    """
    pid = s.opik_default_project_id
    pname = s.opik_default_project_name
    if not pid and not pname:
        return ""
    if pid and pname:
        ref = f'`project_id="{pid}"` (name: "{pname}")'
    elif pid:
        ref = f'`project_id="{pid}"`'
    else:
        ref = f'`project_name="{pname}"`'
    return (
        f"\nThe user's default project is {ref}. Pass it as `project_id` "
        "(or `project_name`) to any tool that accepts one (ask_ollie, list, "
        "score) unless the user explicitly names a different project.\n"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_instructions.py -v`
Expected: all pass (including the existing ones — the template still renders correctly).

- [ ] **Step 5: Commit**

```bash
git add src/opik_mcp/instructions.py tests/test_instructions.py
git commit -m "feat: inject default project into instructions blob

When OPIK_DEFAULT_PROJECT_ID/NAME are set, prepend a sentence telling
the LLM to pass that project on every tool call. Stateless tools, single
seam for project hints.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Accept `project_id` on the `score` tool with id→name resolution

**Files:**
- Modify: `src/opik_mcp/score_comment.py`
- Modify: `src/opik_mcp/server.py`
- Test: `tests/test_score_comment.py`

Context: the opik-backend thread-feedback endpoint only accepts `project_name` (see `opik_client.py:113-135`). To stay consistent with `ask_ollie` (which prefers `project_id`), `run_score` accepts both and resolves id→name via `opik_client.get_project()` when only an id is supplied.

- [ ] **Step 1: Read current run_score test patterns**

Run: `grep -n "thread\|project_name" tests/test_score_comment.py | head -20`

This shows you the existing fake client and thread-score test pattern. The fake client must gain a `get_project` method returning `{"name": ...}`.

- [ ] **Step 2: Extend the test fake**

In `tests/test_score_comment.py`, find the `FakeOpikClient` class. Add (or extend) the `get_project` method to return a fixed mapping:

```python
async def get_project(self, project_id: str) -> dict[str, Any]:
    self.get_project_calls += 1
    if project_id not in self.projects_by_id:
        raise KeyError(project_id)
    return self.projects_by_id[project_id]
```

And in the dataclass/init, ensure `projects_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)` and `get_project_calls: int = 0` exist. (If the existing fake already has these — see `tests/test_opik_client_read.py` which has similar shapes — copy the pattern. Otherwise add.)

- [ ] **Step 3: Write failing tests**

Append to `tests/test_score_comment.py`:

```python
@pytest.mark.anyio
async def test_score_thread_with_project_id_resolves_to_name() -> None:
    client = FakeOpikClient()
    client.projects_by_id["proj-uuid-123"] = {"id": "proj-uuid-123", "name": "chatbot-prod"}
    await run_score(
        target=Target(type="thread", id="thread-xyz"),
        name="helpful",
        value=1.0,
        project_id="proj-uuid-123",
        settings=_settings(),
        client=client,
    )
    assert client.get_project_calls == 1
    # Resolved name was forwarded as project_name to add_thread_feedback_score.
    assert client.thread_feedback_calls == [
        ("thread-xyz", "chatbot-prod"),
    ]


@pytest.mark.anyio
async def test_score_thread_with_project_name_skips_resolution() -> None:
    client = FakeOpikClient()
    await run_score(
        target=Target(type="thread", id="thread-xyz"),
        name="helpful",
        value=1.0,
        project_name="chatbot-prod",
        settings=_settings(),
        client=client,
    )
    assert client.get_project_calls == 0
    assert client.thread_feedback_calls == [("thread-xyz", "chatbot-prod")]


@pytest.mark.anyio
async def test_score_thread_project_id_wins_when_both_provided() -> None:
    client = FakeOpikClient()
    client.projects_by_id["proj-uuid-123"] = {"id": "proj-uuid-123", "name": "chatbot-prod"}
    await run_score(
        target=Target(type="thread", id="thread-xyz"),
        name="helpful",
        value=1.0,
        project_id="proj-uuid-123",
        project_name="stale-cached-name",
        settings=_settings(),
        client=client,
    )
    assert client.get_project_calls == 1
    # project_id resolution wins; the stale project_name is ignored.
    assert client.thread_feedback_calls == [("thread-xyz", "chatbot-prod")]


@pytest.mark.anyio
async def test_score_trace_ignores_project_id() -> None:
    """Trace UUIDs are workspace-unique; project hints aren't consulted."""
    client = FakeOpikClient()
    await run_score(
        target=Target(type="trace", id="trace-abc"),
        name="helpful",
        value=1.0,
        project_id="proj-uuid-123",
        settings=_settings(),
        client=client,
    )
    assert client.get_project_calls == 0  # no resolution needed for trace
```

The fake's `thread_feedback_calls` is a list of `(thread_id, project_name)` tuples — add this attribute to the fake's `add_thread_feedback_score` method to record what was forwarded.

- [ ] **Step 4: Run tests to verify failure**

Run: `uv run pytest tests/test_score_comment.py -v -k "project_id or project_name"`
Expected: failures — `run_score` doesn't accept `project_id`.

- [ ] **Step 5: Update run_score signature and resolution logic**

In `src/opik_mcp/score_comment.py`, modify `run_score`. Replace the signature and the thread branch:

```python
async def run_score(
    *,
    target: Target,
    name: str,
    value: float,
    reason: str | None = None,
    category_name: str | None = None,
    project_id: str | None = None,
    project_name: str | None = None,
    settings: Settings | None = None,
    client: _OpikClientProto | None = None,
) -> ScoreResult:
    """Attach a feedback score to a trace, span, or thread.

    For traces and spans, the entity id alone identifies it server-side —
    project hints are ignored. For threads, the opik-backend endpoint accepts
    only ``project_name``; this function resolves ``project_id`` to a name via
    GET /v1/private/projects/{id} when the caller supplies only the id. If
    both are given, ``project_id`` wins (unambiguous).
    """
    settings = settings or get_settings()
    opik = client if client is not None else _make_client(settings)
    score = FeedbackScore(
        name=name,
        value=value,
        reason=reason,
        category_name=category_name,
    )

    if target.type == "trace":
        await opik.add_trace_feedback_score(target.id, score)
    elif target.type == "span":
        await opik.add_span_feedback_score(target.id, score)
    else:  # "thread"
        resolved_name = await _resolve_project_name_for_thread(
            opik, project_id=project_id, project_name=project_name
        )
        await opik.add_thread_feedback_score(target.id, score, project_name=resolved_name)

    logger.info(
        "score.added target_type=%s target_id=%s name=%s",
```

Add a helper above `run_score` (and update the `_OpikClientProto` protocol to declare `get_project`):

```python
async def _resolve_project_name_for_thread(
    opik: _OpikClientProto,
    *,
    project_id: str | None,
    project_name: str | None,
) -> str | None:
    """Return the project_name to forward to the thread-feedback endpoint.

    project_id wins when both are supplied (unambiguous over a free-form name
    that may be stale). When only project_id is given, fetch the project to
    get its current name. Neither set → return None (backend uses default).
    """
    if project_id:
        project = await opik.get_project(project_id)
        name = project.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"get_project({project_id!r}) returned no name; cannot score thread."
            )
        return name
    return project_name
```

And the `_OpikClientProto` (currently in the same file) needs:

```python
class _OpikClientProto(Protocol):
    ...existing methods...
    async def get_project(self, project_id: str) -> dict[str, Any]: ...
```

- [ ] **Step 6: Expose `project_id` on the MCP score tool**

In `src/opik_mcp/server.py`, find the `score` tool. Add a `project_id` parameter alongside the existing `project_name`. Order matters: list `project_id` before `project_name` in the signature so the LLM sees it first.

Concretely, replace the existing `project_name` annotation block with:

```python
    project_id: Annotated[
        str | None,
        Field(
            description=(
                "Only consulted when `target.type` is 'thread'. Disambiguates the "
                "thread by project when the same thread id exists in multiple "
                "projects. UUID is preferred over `project_name` (unambiguous, "
                "doesn't go stale). Ignored for trace/span targets."
            ),
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        Field(
            description=(
                "Same role as `project_id`, when only the name is known. If you "
                "have the UUID, pass `project_id` instead. Ignored for "
                "trace/span targets."
            ),
            max_length=200,
        ),
    ] = None,
```

And update the body of the tool to forward `project_id`:

```python
    return await run_score(
        target=target,
        name=name,
        value=value,
        reason=reason,
        category_name=category_name,
        project_id=project_id,
        project_name=project_name,
    )
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_score_comment.py tests/test_ask_ollie.py -v`
Expected: all pass.

Run: `uv run mypy src/opik_mcp/score_comment.py src/opik_mcp/server.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/opik_mcp/score_comment.py src/opik_mcp/server.py tests/test_score_comment.py
git commit -m "feat: accept project_id on score tool with id→name resolution

Consistency with ask_ollie. Thread-feedback endpoint takes name only, so
we resolve id→name server-side via GET /projects/{id}. project_id wins
when both are given.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Live test for `ask_ollie` with `project_id=None`

**Files:**
- Modify: `tests/test_ask_ollie_live.py`

Context: this answers Gap 5 from the user-flow analysis — does Ollie tolerate a session with no project scope? The test is gated by `RUN_LIVE_DEV_COMET=1` so it doesn't run in default `make check`.

- [ ] **Step 1: Read existing live test for the env-gating pattern**

Run: `head -50 tests/test_ask_ollie_live.py`

You'll see the pattern: a module-level `pytest.importorskip` or `pytest.mark.skipif` guards on `os.environ.get("RUN_LIVE_DEV_COMET")`. Follow that pattern.

- [ ] **Step 2: Add the live test**

Append to `tests/test_ask_ollie_live.py` (placement: end of file, after any existing happy-path test):

```python
@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_DEV_COMET"),
    reason="live test requires RUN_LIVE_DEV_COMET=1 and dev.comet.com creds",
)
async def test_ask_ollie_without_project_returns_workspace_wide_answer() -> None:
    """Pin down Ollie's behavior when no project_id is set.

    The session-level project_id contextvar will be None; Ollie's read tools
    must handle that gracefully (workspace-wide query or clear error). This
    test documents the actual behavior — if it fails, downstream design
    (e.g. whether the LLM must always pass project) changes.
    """
    result = await run_ask_ollie(
        query="How many projects do I have in this workspace?",
    )
    # The bar: Ollie didn't crash, produced a response. We're not asserting
    # content because the workspace state varies; we're asserting tolerance.
    assert result.complete is True, (
        f"Ollie did not complete without project_id. text={result.text!r}"
    )
    assert len(result.text) > 20, (
        f"Suspiciously short response: {result.text!r}"
    )
```

If the existing file does not import `os`, add `import os` at the top.

- [ ] **Step 3: Run the live test locally (if creds available)**

Run: `RUN_LIVE_DEV_COMET=1 OPIK_API_KEY=... COMET_WORKSPACE=... COMET_URL_OVERRIDE=https://dev.comet.com uv run pytest tests/test_ask_ollie_live.py::test_ask_ollie_without_project_returns_workspace_wide_answer -v`

Expected outcomes (record which one happens):
- PASS → Ollie tolerates `project_id=None`, workspace-wide fallback works.
- FAIL with timeout or error → Ollie's tools fail without a project; the LLM must always pass one. Update `instructions.py:_TEMPLATE` to say "Always pass project_id to ask_ollie."

If you cannot run the live test (no creds), just commit the code and note the observation as a follow-up.

- [ ] **Step 4: Run the default suite to confirm the test skips properly**

Run: `uv run pytest tests/test_ask_ollie_live.py -v`
Expected: existing tests behave as before; the new test reports SKIPPED unless `RUN_LIVE_DEV_COMET=1`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ask_ollie_live.py
git commit -m "test: live test for ask_ollie without project_id

Pins down Ollie's fallback behavior when session.project_id is None.
Gated by RUN_LIVE_DEV_COMET=1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Document the project-scoping flow

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md`

- [ ] **Step 1: Add env vars to README config table**

In `README.md`, find the "Configuration env vars" table (around line 131). Insert two new rows after `OPIK_URL`:

```
| `OPIK_DEFAULT_PROJECT_ID` | _unset_ | UUID surfaced to the LLM via the instructions blob as the user's default project. Tools remain stateless — the LLM passes it on each call. |
| `OPIK_DEFAULT_PROJECT_NAME` | _unset_ | Human-readable companion to `OPIK_DEFAULT_PROJECT_ID`. Either or both can be set; the id is preferred when present. |
```

- [ ] **Step 2: Add design.md subsection**

In `docs/design.md`, find an appropriate place (after the tool surface section, before any deployment notes). Insert:

```markdown
### Project scoping

Opik entities live inside a project, but tool calls follow three patterns
for resolving which project to use:

1. **Entity-implicit.** When the call carries a UUID (trace, span,
   experiment, etc.), the project is resolved server-side from the entity —
   `score`/`comment` on a trace don't need a project hint.

2. **LLM-discovered.** For listing or for `ask_ollie` queries, the LLM
   either reads the default from the instructions blob (if
   `OPIK_DEFAULT_PROJECT_ID`/`NAME` is configured) or calls `list(project)`
   to find one, then threads `project_id` through every subsequent call.

3. **LLM-asked.** When ambiguous and no default is set, the LLM should
   ask the user which project.

There is no server-side "active project" state. The single seam for hints
is the `InitializeResult.instructions` blob, rendered once per MCP session
by `instructions.py`. Tools stay stateless so a single MCP server can
serve many users (relevant for Phase 2).

Ollie's pod separately maintains per-session project state via its
`PageContext` envelope (`ask_ollie` forwards `project_id`/`project_name`
on every request — see `ask_ollie.py:142-153`). Ollie clears this state on
each request that omits `context`, so the LLM must re-send project on
every follow-up, including thread continuations.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/design.md
git commit -m "docs: project-scoping section in design.md + env vars in README

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run `uv run pytest -q` (default — skips live tests).
- [ ] Pre-existing `tests/test_read_list/*` failures are unrelated to this work and may still be red.
- [ ] Run `uv run mypy src/opik_mcp/config.py src/opik_mcp/instructions.py src/opik_mcp/score_comment.py src/opik_mcp/server.py` — expect clean.

---

## Self-review notes

**What's deliberately deferred:**
- Gap 2 (entity_hint on ask_ollie for trace UUIDs) — defer until Task 4 surfaces whether `project_id=None` is actually broken on Ollie's side. If it works, this gap dissolves.
- Gap 4 (multi-project comparison) — accepted as N round-trips by the LLM; not worth structural work.
- Gap 6 (Phase-1 write tools) — those tools don't exist yet; their project handling will mirror Task 3's pattern when built.
- Gap 7 (page_context overlap) — sharpened in the description already; further work is overreach.
- Gap 8 (cross-host config drift) — out of scope, user-side problem.

**Why instructions-only, not server-side fallback:**
A server-side "if project_id is None and OPIK_DEFAULT_PROJECT_ID is set, inject it" rule hides state and breaks the Phase-2 multi-tenant model where settings come from the request's auth claim, not env. Keeping the seam at the instructions blob means the LLM stays the single arbiter of which project to use — explicit, debuggable, and tenant-safe.

**Why `project_id` wins over `project_name`:**
UUIDs don't go stale. A user who renamed `chatbot-v1` to `chatbot-v2` last week and still has `project_name="chatbot-v1"` cached in their MCP host config would silently target the wrong project. `project_id` resolved fresh on the backend is correct.

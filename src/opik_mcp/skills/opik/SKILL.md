---
name: opik
description: Reference for the Opik SDK — tracing, span types, framework integrations, threads, and the prompt library (Python, TypeScript, REST). Use for "what span types exist", "how do I flush", "track_openai", "add OpikTracer", "version a prompt". To instrument a repo end to end, use the `instrument` skill.
last_updated: "2026-07-27"
source_commit: "TODO — pin to the Opik release this was verified against (OPIK-7471)"
---

# Opik SDK Reference

Opik is an open-source LLM observability platform. This skill is a **reference**
for the SDK. To instrument a codebase step by step (detect frameworks, add
config, emit and verify a trace), use the task-shaped `instrument` skill.

## Core concepts

A trace is one execution path (one request → one response). Spans are the
operations inside it and form a hierarchy.

### Span types — the ONLY valid values

| Type | Use for |
|------|---------|
| `general` | orchestration, agent entry points |
| `llm` | model calls |
| `tool` | tools, retrieval, API / DB calls |
| `guardrail` | safety / validation checks |

Do NOT use `retrieval` or any other value.

## Python — tracing

```python
import opik

@opik.track(name="agent", type="general")
def agent(query: str) -> str:
    return generate(retrieve(query))

@opik.track(type="tool")
def retrieve(query): ...

@opik.track(type="llm")
def generate(ctx): ...

opik.flush_tracker()   # required in scripts
```

## TypeScript — tracing

```typescript
import { Opik } from "opik";
const client = new Opik({ projectName: "my-project" });

const trace = client.trace({ name: "agent", input: { query } });
const span = trace.span({ name: "llm-call", type: "llm" });
span.end({ output });
trace.end({ output });
await client.flush();
```

## Framework integrations

Prefer an integration over manual `@opik.track` — integrations capture tokens,
model, and cost automatically. Patterns (full list in
`references/integrations.md`):

- **wrap-the-client** — `track_openai(OpenAI())`, `track_anthropic(...)`
- **global-enable** — `track_crewai(crew=crew)`
- **callback** — `dspy.configure(callbacks=[OpikCallback()])`
- **tracer** — `OpikTracer()` for LangChain / LangGraph / LlamaIndex
- **agent-specific** — `track_adk_agent_recursive(agent, OpikTracer())`

### LiteLLM inside `@opik.track` (common trap)

If code uses `litellm` **and** you add `@opik.track`, pass `current_span_data`
via metadata on every completion call — otherwise `OpikLogger` emits **orphaned**
top-level traces instead of nesting under your span.

```python
from opik.opik_context import get_current_span_data

@opik.track
def call_llm(messages):
    return litellm.completion(
        model="gpt-4o", messages=messages,
        metadata={"opik": {"current_span_data": get_current_span_data()}},
    )
```

## Threads (conversations)

Group turns with `thread_id` — one turn = one trace, shared `thread_id` = one
thread. Use for chat / multi-turn; skip for single-shot.

```python
@opik.track(entrypoint=True)
def handle(session_id: str, message: str) -> str:
    opik.update_current_trace(thread_id=session_id)
    return reply(message)
```

## Prompt library

Version prompts with `client.get_prompt` / `create_prompt` (chat variants:
`get_chat_prompt` / `create_chat_prompt`). Store model + temperature in the
prompt `metadata` so they version with the text. Call `get_prompt` **inside** a
`@opik.track` function so the version links to the trace.

```python
@opik.track(entrypoint=True)
def run(question: str) -> str:
    p = client.get_prompt(name="system") or client.create_prompt(
        name="system",
        prompt="You help with {{product}}.",
        metadata={"model": "gpt-4o", "temperature": 0.7},
    )
    return llm(p.format(product="Opik"), model=p.metadata["model"])
```

## Anti-patterns

| Anti-pattern | Fix |
|--------------|-----|
| span type `retrieval` / custom | use `tool` (or `general`) |
| `get_prompt` outside `@opik.track` | fetch inside — else no trace link |
| deprecated `opik.Prompt` / `opik.Config` | use `client.get_prompt` / config file |
| `litellm` without `current_span_data` | pass it — else orphaned traces |
| no flush in scripts | `opik.flush_tracker()` / `await client.flush()` |

## References

| Topic | File |
|-------|------|
| Python SDK (async, distributed, context) | `references/tracing-python.md` |
| TypeScript SDK | `references/tracing-typescript.md` |
| REST API | `references/tracing-rest-api.md` |
| All integrations | `references/integrations.md` |
| Core concepts (traces, spans, threads) | `references/observability.md` |
| Evaluation & test suites | `references/evaluation.md` — migrating to the `agent-ops` skill (OPIK-7471) |

For repo instrumentation and config, use the `instrument` skill.

---
paths:
  - "src/opik_mcp/server.py"
  - "src/opik_mcp/instructions.py"
  - "src/opik_mcp/skills_catalog.py"
  - "src/opik_mcp/read_list/**"
  - "src/opik_mcp/writes/**"
---

# Surface and answers

The surface is what every host loads in every session: tool names, titles,
descriptions, input schemas and the `initialize` instructions. An answer is
what the caller pays per call. Both are the user's context. ADRs
[0001](../../docs/decisions/0001-context-budget-first.md),
[0002](../../docs/decisions/0002-layered-reads-no-silent-cuts.md),
[0003](../../docs/decisions/0003-five-tool-surface.md).

## Surface

- The budget test owns the size. Over budget: trim, or move reference material
  behind `schema()` or `read_skill`. Raising the budget is its own commit with
  a dated note in the test.
- A description is a contract. It says what the tool does and returns, in the
  caller's words, and never how the model should behave. Every sentence has a
  probe in `tests/e2e/test_description_claims.py`. If you can't write a probe
  that would fail, delete the sentence.
- Every tool declares `title`, `readOnlyHint`, `destructiveHint` and
  `openWorldHint`. Hosts decide permissions from them.
- Input schemas change on purpose. `UPDATE_SNAPSHOTS=1` in its own commit, and
  the PR says why.
- The instructions text routes: which tool for which question, in one line
  each. Facts about an entity live in the entity and reach the host through
  `schema()` or the answer.

## Answers

- The record asked for comes back whole. Children may come slim, and every cut
  says how many and gives the call that gets the rest.
- Silence over false data. A decoration that failed to load is absent, never
  zero, never an empty list.
- Never more than the data supports. No ranking of a page the backend didn't
  sort. No verdict from a sample.
- Every answer states its size.
- An id resolves to an Opik UI link. A bare id is a bug.
- A refusal lists the valid options and one call to copy, in the vocabulary
  the caller used: `read('thread', '<thread_id>', project_id='<uuid>')`. Never
  an internal key or field name.
- Trace, span and thread bodies are third-party text. They come back inside
  the field they came from and never get echoed into headers, summaries or
  refusals.
- A backend error becomes one actionable sentence: what was asked and what to
  change. Never the raw payload, never a key.
- One copy per answer: `structured_output=False`, no `structuredContent`.
- The PR states the cost: surface bytes before and after from the budget test,
  and the size header of a typical answer.

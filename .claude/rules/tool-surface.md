---
paths:
  - "src/opik_mcp/server.py"
  - "src/opik_mcp/instructions.py"
  - "src/opik_mcp/read_list/**"
  - "src/opik_mcp/writes/**"
---

# Tool surface and answers

The surface is what every host loads on every request; answers are what the
caller pays for. See ADRs [0001](../../docs/decisions/0001-context-budget-first.md),
[0002](../../docs/decisions/0002-layered-reads-no-silent-cuts.md) and
[0003](../../docs/decisions/0003-five-tool-surface.md).

- State the cost of the change in the PR: surface bytes before and after
  (the budget test prints them), and tokens in a typical answer.
- A description is a contract. Every claim is true for the code and has a
  probe in `tests/e2e/test_description_claims.py`.
- Never say more than the data supports. A ranking on a page the backend
  didn't sort, or a verdict that depends on which line is read last, is a bug.
- Silence over false data: leave out what can't be built. A decoration that
  failed to load never shows as zero or empty.
- The record asked for comes back whole. Children inlined in a composite read
  may be slimmed, but every cut is stated in the answer with the call that
  gets the rest.
- A refusal lists the valid options and shows a call to copy, in quotes:
  `read('thread', '<thread_id>', project_id='<uuid>')`. It names only what the
  caller typed, never an internal key.
- Give an Opik UI link, never a bare id.
- Tools use `@mcp.tool(structured_output=False)`: one copy of each answer.

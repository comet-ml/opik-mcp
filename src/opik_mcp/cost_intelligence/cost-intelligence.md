---
name: cost-intelligence
description: Answer questions about an organization's Claude Code spend from a cost intelligence (AI Spend) workspace — who spends most, what the money goes to, and what happened in a session. Read before the first spend answer.
---

# Cost intelligence

This workspace holds one organization's Claude Code usage, in the `claude-code`
project. The spend_* types always query that project. Every other type takes any
project, as in any workspace.

## Where the numbers come from

Dollars come only from the `spend_*` types. Opik's own cost fields on traces, threads and
`read('project')` are empty here and read $0 — never report them.

There are two dollar figures. Always say which one you quote:

- **Billed** — what the organization pays: the seat fee for the window, plus over-plan and
  API charges. The leaderboard (`spend_user`) and `spend_summary` use it. Everyone inside
  the same plan shows the same seat price, so their tokens are what tells them apart.
- **List price** — what the tokens would cost at API rates. Lanes, lane items and
  `spend_agent` use it. Lane items also carry the billed share, which is zero inside a plan.

Rankings are by total tokens: input, cache reads, cache writes and output, weighted
equally. A cache read costs about a tenth of fresh input and output costs several times
more, so the session with the most tokens is not always the priciest. Say "by tokens" when
you rank.

## Lanes

A lane is a spend category on the AI Spend home page. Input lanes are what each request
sends to the model; they are re-sent every turn, so long sessions pay for them again and
again.

| Lane | What it holds |
|---|---|
| `user_prompts` | Text people typed. |
| `file_attachments` | Files attached to prompts. |
| `built_in_tools` | Built-in tool definitions (Bash, Read, Edit…) and their results. |
| `prior_assistant` | The model's own earlier replies and thinking, replayed. The cost of session length. |
| `skills` | The skills menu and loaded skill bodies. |
| `custom_agents` | Subagent descriptions. |
| `mcp_servers` | MCP tool definitions, server instructions and tool results. |
| `memory` | CLAUDE.md, rules and auto-memory. |
| `static_overhead` | The system prompt, environment block and other fixed harness text. |
| `unattributed` | Billed tokens no block claimed. It has no breakdown. |

Output lanes are what the model writes: `thinking`, `assistant_text`,
`built_in_tool_calls`, `mcp_tool_calls`, `skill_invocations`.

Inside a lane, an item's **definition** tokens are always-on context, paid on every
request whether or not anyone uses the item. **Usage** tokens are paid when it runs.
**Recoverable** cost is the definition cost for users who never use the item: the saving
from removing it for them.

## What is logged

Categories, not an exhaustive key list:

- **Session** (trace metadata): who ran it (email, display name, organization), the harness,
  the repository (remote, branch, commit), and whether content capture is on.
- **Turn** (one trace): the prompt and the response, only when capture is on. Tags
  `user:<email>`, `harness:<name>`, `org:<name>`.
- **Model call** (an `llm` span): the model; token usage by tier under
  `usage.original_usage.*`; settings (effort, thinking, max tokens); what triggered it (a
  user turn or an automated call); and its content blocks, each with a category, side,
  size, hash and cache status. Block text only when capture is on.
- **Tool call** (a `tool` span named `tool_use: <Tool>`): the tool name.
- **Never logged**: credentials. With capture off (the default), there is no prompt,
  response or thinking text, no tool arguments or results, and no diffs.

A thread is one Claude Code session. Its id is the session id the `spend_session` type
uses. A trace is one turn; its name is the start of the prompt, or `automated…` for
background calls (titles, suggestions). Status-line prompts, recaps and messages from
other sessions are also logged as turns, under their own names. Skip all of these when
you describe what someone did.

## Traps

- A model-call span's input and output hold the raw API request and the streamed response,
  often thousands of tokens. Don't read them unless asked; narrow with `fields=`.
- Opik's `usage.total_tokens` leaves cache tokens out. They are under
  `usage.original_usage.*`.
- Sessions can run past a thousand turns. Outline them with a trace list before reading
  any turn in full.
- When capture is off there is no prompt text. Describe the work from tool calls, lanes and
  the narrative, and say the text wasn't captured.

## Recipes

**Who spends the most?** `list('spend_user')` ranks by tokens. Quote the billed figure and
seat type, and say that users inside a plan share the same seat price.

**What are we spending on?** `list('spend_lane')` lists lanes by list price. Then
`read('spend_lane', '<lane>')` names the servers, skills or tools inside a lane.
`list('spend_user', filters='mcp_server = "<name>"')` (or `skill`, `built_in_tool`) shows
who uses one of them.

**How are we doing?** `list('spend_summary')` compares the window with the one before.

**What did the most expensive session do?**
1. `list('spend_session', size=5)` lists sessions by tokens.
2. `read('spend_session', '<id>')` gives the narrative: tasks, phases and a summary.
3. If the narrative isn't ready, outline the session, oldest turn first:
   `list('trace', project_name='claude-code',
   filters='thread_id = "<id>" AND name not_contains "automated"', fields=['name'],
   sort='start_time asc', size=50)`. The filter drops only turns named `automated…`.
   Status-line prompts, recaps and cross-session messages remain and show in their names,
   so skim past them. With capture on, a turn's name starts with its prompt. With capture
   off, names are `user_turn` or `tool_continuation: <tool>`, and the outline shows only
   the turn count and tool names.
   Then read only the turns that matter with `read('trace', '<trace id>')`.

**Which subagents cost the most?** `list('spend_agent')`.

**"Me" or "us"?** "I", "me" and "my" mean the caller: filter by their email, or ask for it
if you don't know it. "We", "our team" and "the org" mean no user filter.

Every spend type takes `since` and `until` and defaults to the last 30 days. The AI Spend
pages linked in each answer show the same figures.

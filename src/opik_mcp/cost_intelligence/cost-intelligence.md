---
name: cost-intelligence
description: Read a Claude Code session in a cost intelligence (AI Spend) workspace from its Opik records — what is logged, how threads and traces map to sessions and turns, and how to outline a session. Read before the first answer.
---

# Cost intelligence

This workspace holds one organization's Claude Code usage. Every call is scoped to the
`claude-code` project; other projects are refused.

Opik's own cost fields on traces, threads and `read('project')` are empty in this
workspace and read $0. Never report them.

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

A thread is one Claude Code session. A trace is one turn; its name is the start of the
prompt, or `automated…` for background calls (titles, suggestions, recaps). Skip automated turns when you describe
what someone did.

## Traps

- A model-call span's input and output hold the raw API request and the streamed response,
  often thousands of tokens. Don't read them unless asked; narrow with `fields=`.
- Opik's `usage.total_tokens` leaves cache tokens out. They are under
  `usage.original_usage.*`.
- Sessions can run past a thousand turns. Outline them with a trace list before reading
  any turn in full.
- When capture is off there is no prompt text. Describe the work from tool calls, and say
  the text wasn't captured.

## Recipes

**What did a session do?** Outline it, oldest turn first, without automated turns:
`list('trace', filters='thread_id = "<id>" AND name not_contains "automated"',
fields=['name'], sort='start_time asc', size=50)`. Each name is the start of a prompt. Then
read only the turns that matter with `read('trace', '<trace id>')`.

**"Me" or "us"?** "I", "me" and "my" mean the caller: filter by their email, or ask for it
if you don't know it. "We", "our team" and "the org" mean no user filter.

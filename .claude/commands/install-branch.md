---
# Commands, not skills: anything under .claude/skills/ is picked up by
# `npx skills add` and would ship to users. Side effects, so only a person
# can invoke it.
disable-model-invocation: true
description: Install the current worktree as MCP server opik-<ticket>, for sessions in this repo only.
argument-hint: "[NAME=main] [WORKSPACE=other-workspace]"
---

Install this worktree as an MCP server and tell me how to load it.

1. Run `make install-branch $ARGUMENTS`. The server name comes from the branch
   (`OPIK-8480-…` becomes `opik-8480`); `NAME=` and `WORKSPACE=` override it.
   Credentials come from `OPIK_*` env vars, then `~/.opik.config`. Never pass
   an API key on the command line and never print one.
2. If it fails for missing credentials, tell me to run `opik configure` or set
   the env vars. Don't try to find a key yourself.
3. Check the result with `claude mcp get opik-<name>` and report the status line,
   URL and workspace. Don't show the environment block; it holds the key.
4. Say what to do next, based on the last line the target printed:
   - "Registered": a new server. Restart Claude Code to load it.
   - "Reinstalled": run `/mcp` and reconnect `opik-<name>`.

The server is registered at local scope, so it loads only in sessions inside
this repo; each one adds its instructions to them (budgeted in
`tests/conformance/test_tool_inventory.py`). For a
one-off comparison with main, use `/dogfood` instead: it registers nothing.

The install is a snapshot of the tree. After changing code, run it again.
`make uninstall-branch` removes the server and its venv; do that when done.

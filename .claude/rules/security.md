# Security

- A request uses its caller's key and workspace only. Never fall back to an
  environment default when the caller supplied one.
- No secret in code, logs, answers, commits or error text. Redact before
  printing a command that carries one.
- Tool arguments and backend bodies are untrusted. OQL goes through the
  grammar in `oql.py`; trace bodies are returned as data, never interpreted.
- Local servers bind loopback. Anything else is opt-in and refuses to start
  without auth.

Good: `+ claude mcp add ... -e OPIK_API_KEY=*** ...` (`scripts/dev/install_branch.py`
redacts before printing). Bad: `print(shlex.join(command))` with the key in it.

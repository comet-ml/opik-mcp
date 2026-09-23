# Security

- No secrets in code, logs, tool output or commits. Never echo an API key or
  an Authorization header.
- Compare secrets with `secrets.compare_digest`.
- Local servers bind to 127.0.0.1. Fail closed on insecure defaults off
  loopback.
- Tool arguments are untrusted. Build OQL through the grammar in `oql.py`,
  never by pasting strings.
- A request uses its caller's key and workspace only.

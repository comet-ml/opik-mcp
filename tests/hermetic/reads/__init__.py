"""The read surface, one file per entity, end to end against the stub backend.

WHAT THIS COVERS THAT NOTHING ELSE DOES. ``tests/read_list`` drives
``run_read`` and ``run_list`` against a fake client: it proves the logic and
the wording and cannot see the wiring. A handler that stops being registered,
a request body that no longer matches what the endpoint takes, a record whose
``project_id`` the fetcher drops before the link is built: none of that fails
a unit test whose fake accepts any keyword. Here the server is the real
subprocess, the stub answers on a real socket, and each test checks both
halves: the answer the agent reads and the request the backend saw.

Every entity file covers the same four things: the envelope, the size in the
header, the link, and a refusal that carries no backend body or REST path.

WHAT NONE OF THIS REACHES, AND WHO DID. Whether a link arrives in front of a
person depends on the host: whether it passes the ``initialize`` instructions
to the model at all, and whether what the model then writes is rendered as a
link. No test here can see either, so the manual pass is recorded here, in a
tracked file, because ``docs/`` keeps only design docs and decisions:

===================================  ==========  ====================================
Host                                 Walked      What happened
===================================  ==========  ====================================
Claude Code                          2026-09-22  Instructions passed through; rendered
                                                 as a named link; opened correctly.
Cursor                               —           not walked
VS Code Copilot MCP                  —           not walked
MCP Inspector                        n/a         no model; shows the raw payload,
                                                 which is the point of it.
Custom agents (SDK, openai-mcp)      —           not walked
===================================  ==========  ====================================

An unwalked row is not a passing row. The failure it hides is silent: a host
that drops ``instructions`` leaves the model with a ``url`` key among thirty
others, and the user gets an id.
"""

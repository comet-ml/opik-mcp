"""Every write operation, end to end, over both transports.

WHAT THIS COVERS THAT NOTHING ELSE DOES. ``tests/writes`` calls the
dispatcher in-process with respx standing in for httpx, so it proves the
request each operation builds and cannot prove what reaches a backend from a
running server: that the tool's arguments survive the MCP layer, that the
client sends the method and body the builder chose, that the url is built from
this session's workspace, and that an HTTP request forwards its caller's
bearer rather than anything the process holds.

One file per module of ``src/opik_mcp/writes/operations/``. Each case says
what one call must send and what it must answer. The tables are checked
against the registry, so an operation added without a case fails
``test_every_write_operation_has_an_end_to_end_case``.

Every case runs over stdio (a server per test, holding its own key) and over
Streamable HTTP (one uvicorn process for the package, an API-key bearer per
session). The stub stores nothing, so each case asserts on the requests it
recorded, not on a read-back.
"""

"""Thin async wrapper around Opik's REST API.

Read methods map 1:1 to a single REST endpoint and are consumed by the
``read`` / ``list`` registry. Writes go through the universal ``write``
tool's dispatcher (``writes/dispatch.py``) which calls
``OpikClient.write_json`` directly with templated paths and pre-built
bodies — no per-endpoint helper. Workspace is bound at construction time
and sent on every request via the ``Comet-Workspace`` header; the MCP
tool surface never takes a workspace argument (see
``docs/runtime/design-doc.md``).
"""

from __future__ import annotations

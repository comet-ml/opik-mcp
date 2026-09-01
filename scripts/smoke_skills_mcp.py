"""Real-MCP smoke run for the skills surface (OPIK-7472).

Spawns `python -m opik_mcp` as a **subprocess over stdio** — the transport Claude
Code and Cursor actually use — and drives it with a real client session: list the
skill resources, read one, then call `read_skill` in every form it documents.

Deliberately a subprocess rather than an in-process server: `__main__`'s stdio
branch and `server.build_app()` install the resource handlers separately, and only
this path exercises the former. Needs no Opik credentials and makes no backend
calls — skill content ships in the wheel.

Run: ``uv run python scripts/smoke_skills_mcp.py``
"""

from __future__ import annotations

import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from opik_mcp.skills_catalog import SKILLS_URI_PREFIX

SERVER = StdioServerParameters(
    # The interpreter running this script, so the smoke run uses the same
    # environment as the checkout rather than whatever `python` resolves to.
    command=sys.executable,
    args=["-m", "opik_mcp"],
    env={
        **os.environ,
        # A local poke is not product usage; keep it out of the BI funnel.
        "OPIK_MCP_ANALYTICS_ENABLED": "false",
        # Quiet the server's own startup logging on stderr.
        "OPIK_MCP_LOG_LEVEL": "WARNING",
    },
)


def _first_line(result: object) -> str:
    content = getattr(result, "content", None) or []
    text = getattr(content[0], "text", "") if content else ""
    return text.splitlines()[0] if text else "<empty>"


async def main() -> None:
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        print(f"connected: {init.serverInfo.name} (protocol {init.protocolVersion})")

        tools = await session.list_tools()
        print(f"tools:     {', '.join(sorted(t.name for t in tools.tools))}")

        listed = await session.list_resources()
        print(f"resources: {len(listed.resources)}  cache={listed.model_extra}")
        for resource in listed.resources[:3]:
            print(f"           {resource.uri}")
        print(f"           … and {max(0, len(listed.resources) - 3)} more")

        entry_point = next(r for r in listed.resources if str(r.uri).endswith("/opik/SKILL.md"))
        fetched = await session.read_resource(entry_point.uri)
        body = fetched.contents[0]
        chars = len(getattr(body, "text", ""))
        print(f"read:      {entry_point.uri} -> {chars} chars  cache={fetched.model_extra}")

        print("\nread_skill — every documented form:")
        for note, argument in (
            ("a skill", "opik-diagnose"),
            ("a path", "opik/references/best-practices.md"),
            ("a resource URI", f"{SKILLS_URI_PREFIX}opik-evaluate/SKILL.md"),
            ("a sibling citation", "../opik/references/observability.md"),
            ("a bare reference name", "opik/production"),
            ("unknown skill", "instrument"),
            ("outside the tree", "opik/../../../etc/passwd"),
        ):
            result = await session.call_tool("read_skill", {"skill_name": argument})
            flag = "ERROR" if result.isError else "ok   "
            print(f"  {flag} {note:<22} {argument!r}\n        {_first_line(result)[:96]}")


if __name__ == "__main__":
    anyio.run(main)

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field

from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.config import get_settings
from opik_mcp.server.tools.hints import READS
from opik_mcp.skills_catalog import (
    SKILLS_URI_PREFIX,
    feature_skill_names,
    read_skill_tool_description,
    request_shape,
    run_read_skill,
    skill_names,
)


def _read_skill_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    """Analytics labels for ``read_skill``.

    Three low-cardinality labels, three distinct questions.

    ``skill`` is safe as a raw label because the value space is closed — the
    handful of skills bundled in the wheel — but only after the guard below:
    the argument is caller-supplied, so an unknown value must collapse to a
    constant rather than mint a new label per typo. The document path is never
    emitted: 16 of the 21 bundled files are references, so it would be the
    highest-cardinality label on the event for no decision it informs.

    ``request_shape`` says which documented form the caller used, which is how we
    learn whether the resource surface is being discovered at all — a `uri` means
    the caller browsed `resources/list` first. ``is_reference`` says whether they
    read a whole skill or drilled into one of its supporting documents.
    """
    requested = str(kwargs.get("skill_name", "")).strip().strip("/")
    skill = requested.removeprefix(SKILLS_URI_PREFIX).removeprefix("../").partition("/")[0]
    is_reference = not requested.endswith("SKILL.md") and "/" in requested.removeprefix("../")
    known = (*skill_names(), *feature_skill_names(get_settings()))
    return {
        "skill": skill if skill in known else "unknown",
        "request_shape": request_shape(requested),
        "is_reference": str(is_reference).lower(),
    }


# --- read_skill (OPIK-7472) --------------------------------------------- #
#
# The same documents are also served as MCP resources (see ``skills_resources``).
# The tool exists because resource browsing is a host capability, not a model
# one: many hosts surface resources to the user and never to the LLM, so on
# those a resources-only implementation would ship skills no agent can reach.
#
# One argument, several forms: a skill name, a path inside a skill, or the
# resource URI a host that browsed `resources/list` already holds. 16 of the 21
# bundled files are supporting documents a SKILL.md tells the agent to go read,
# so a name-only contract would leave `opik` a 5 KB index with 130 KB of
# unreachable references behind it.
#
# The description's routing list is rendered from the bundled tree, so a new
# skill cannot be shipped unmentioned. Reference paths are not in it: each
# SKILL.md ends with the list of its references, so a caller reads the path
# from the document that cites it instead of paying for an inventory in every
# session.
#
# No `enum` on `skill_name`: the argument accepts paths and URIs as well as the
# five names, so an enum would advertise a closed set the tool does not enforce
# and reject valid calls at the host's schema check.


@instrument_tool("read_skill", props_fn=_read_skill_props)
async def read_skill(
    skill_name: Annotated[
        str,
        Field(
            description=(
                "A skill name ('opik-instrument'), a path inside a skill "
                "('opik/references/tracing-python.md'), or a resource URI "
                "('opik://skills/opik/SKILL.md'). A SKILL.md ends with the list of "
                "its references."
            ),
            min_length=1,
            max_length=512,
        ),
    ],
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    if ctx is not None:
        await ctx.info(f"read_skill.called skill_name={skill_name}")
    return run_read_skill(skill_name, get_settings())


def register(mcp: FastMCP[object]) -> None:
    mcp.tool(
        description=read_skill_tool_description(),
        title="Read an Opik agent skill",
        annotations=READS,
        structured_output=False,
    )(read_skill)

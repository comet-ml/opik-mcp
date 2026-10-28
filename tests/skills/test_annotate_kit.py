"""The kit reference ``opik-annotate`` ships is the version the write operations send.

A view is stored with ``kit_version``, and the page renders it against that
kit. A reference for another version would teach helpers the stored view
cannot call. Re-copy ``references/kit-v1.md`` from opik's
``apps/opik-frontend/src/shared/SandboxedView/kit/v<N>/REFERENCE.md`` and
update ``metadata.source_commit`` when Opik bumps or edits the kit.
"""

from __future__ import annotations

from opik_mcp import skills_catalog as catalog
from opik_mcp.writes.operations.annotation_queue.models import KIT_VERSION
from tests.factories import make_settings

SETTINGS = make_settings(opik_api_key="k", comet_workspace="ws")


def test_the_kit_reference_is_the_version_the_writes_send() -> None:
    answer = catalog.run_read_skill(f"opik-annotate/references/kit-v{KIT_VERSION}.md", SETTINGS)
    body = answer.split("\n", 1)[1].lstrip("\n")
    assert body.startswith(f"# Opik view kit v{KIT_VERSION}\n"), (
        f"opik-annotate/references/kit-v{KIT_VERSION}.md is not the v{KIT_VERSION} kit "
        "reference; re-copy it from opik (see this module's docstring)."
    )
    assert f"`kit_version: {KIT_VERSION}`" in body


def test_the_skill_lists_the_kit_reference_for_the_agent_about_to_write_a_view() -> None:
    answer = catalog.run_read_skill("opik-annotate", SETTINGS)
    assert f"opik-annotate/references/kit-v{KIT_VERSION}.md" in answer

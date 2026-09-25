"""``InitializeResult.instructions`` template (ADR 0004 D6)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.config import Settings
from opik_mcp.instructions import render_instructions
from opik_mcp.read_list.ui_links import trace_link_template
from opik_mcp.server import mcp
from opik_mcp.skills_catalog import (
    SKILL_SUMMARIES,
    read_skill_tool_description,
    skill_names,
)
from tests.factories import make_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "opik_api_key": "k",
        "comet_workspace": "demo-ws",
        "opik_url": "https://opik.test/",
    }
    base.update(overrides)
    return make_settings(**base)


def test_render_substitutes_workspace_and_url() -> None:
    out = render_instructions(_settings())
    assert 'workspace "demo-ws"' in out
    assert "https://opik.test" in out
    assert out.count("https://opik.test") >= 1
    # Trailing slash stripped
    assert "https://opik.test/" not in out


def test_render_uses_comet_url_override_when_opik_url_missing() -> None:
    s = _settings(opik_url=None, comet_url_override="https://demo.comet.com/")
    out = render_instructions(s)
    assert "https://demo.comet.com/opik" in out


def test_render_strips_api_suffix_from_opik_url() -> None:
    """OPIK_URL is the REST API base (…/opik/api); the blob must name the UI
    base (…/opik) — the verbatim ``/api`` leak is OPIK-7033's defect #2.

    The REST base used to appear once more, inside the trace link the blob
    named; that link is gone, so now it appears nowhere. The defect was the
    blob giving the API base as the UI address, which is what this pins.
    """
    s = _settings(opik_url="https://dev.comet.com/opik/api")
    out = render_instructions(s)
    assert "The Opik UI is at https://dev.comet.com/opik." in out
    assert "https://dev.comet.com/opik/api" not in out


def test_render_prefers_resolved_workspace_over_settings() -> None:
    """The OAuth-introspected workspace (set per session) outranks the static
    env workspace — defect #1: the blob must name the authorized workspace."""
    from opik_mcp.auth_context import resolved_workspace_name

    token = resolved_workspace_name.set("andreicautisanu")
    try:
        out = render_instructions(_settings(comet_workspace="env-ws"))
    finally:
        resolved_workspace_name.reset(token)
    assert 'workspace "andreicautisanu"' in out
    assert 'workspace "env-ws"' not in out


def test_render_inbound_workspace_header_outranks_resolved() -> None:
    """An explicit Comet-Workspace header (self-hosted / API-key) is the most
    authoritative signal for the session."""
    from opik_mcp.auth_context import inbound_workspace, resolved_workspace_name

    t_header = inbound_workspace.set("header-ws")
    t_resolved = resolved_workspace_name.set("resolved-ws")
    try:
        out = render_instructions(_settings(comet_workspace=None))
    finally:
        inbound_workspace.reset(t_header)
        resolved_workspace_name.reset(t_resolved)
    assert 'workspace "header-ws"' in out


def test_render_uses_default_workspace_when_unset() -> None:
    """With no workspace configured the tools operate against "default"
    (Opik SDK convention), so the LLM-facing context must say so rather than
    "(workspace not configured)"."""
    from opik_mcp.config import DEFAULT_WORKSPACE

    s = _settings(comet_workspace=None)
    out = render_instructions(s)
    assert f'workspace "{DEFAULT_WORKSPACE}"' in out


def test_render_omits_user_clause_when_email_unknown() -> None:
    out = render_instructions(_settings())
    assert " as " not in out.split("Tool selection:")[0]


def test_render_includes_user_email_when_provided() -> None:
    out = render_instructions(_settings(), user_email="me@example.com")
    assert "as me@example.com" in out


def test_render_includes_today_date() -> None:
    out = render_instructions(_settings(), today=datetime(2026, 5, 15, tzinfo=UTC))
    assert "2026-05-15" in out


def test_render_mentions_tool_selection_guidance() -> None:
    """The blob's reason for existing is to prime tool routing."""
    out = render_instructions(_settings())
    assert "read" in out
    assert "list" in out
    assert "read_skill" in out


def test_render_tells_the_agent_list_can_filter_sort_and_window() -> None:
    """Hosts that inject instructions but lazy-load tool schemas would otherwise
    leave the agent paging through traces and sorting in its head."""
    out = render_instructions(_settings())
    assert "filters" in out and "OQL" in out
    assert "sort" in out and "since" in out and "search" in out
    assert 'since="1h"' in out and 'sort="duration desc"' in out
    assert 'schema("list.trace")' in out
    assert "optional name filter, page, size" not in out


# --- the blob must describe only what this connection advertises ---------- #


def test_the_blob_names_no_tool_that_is_not_advertised() -> None:
    """The general rule, checked against the live registry rather than a list:
    every `name:`-style tool mention in the blob must be a tool this server
    actually advertises. Catches the next tool that gets gated behind a flag."""
    advertised = {t.name for t in mcp._tool_manager.list_tools()}
    out = render_instructions(_settings())

    mentioned = {word.rstrip(":") for word in re.findall(r"^- (\w+)", out, re.MULTILINE)}
    unknown = {m for m in mentioned if m not in advertised and m != "Direct"}
    assert not unknown, f"the blob describes tools that are not advertised: {sorted(unknown)}"


def test_render_includes_default_project_name_when_set() -> None:
    s = _settings(opik_default_project_name="chatbot-prod")
    out = render_instructions(s)
    assert "chatbot-prod" in out
    assert "default project" in out.lower()


def test_render_omits_default_project_when_unset() -> None:
    out = render_instructions(_settings())
    assert "default project" not in out.lower()


@pytest.mark.anyio
async def test_server_advertises_instructions_blob() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        result = await session.initialize()
    assert result.instructions is not None
    assert "Opik" in result.instructions
    assert "Tool selection" in result.instructions


def test_the_handshake_no_longer_names_a_trace_template() -> None:
    """It used to, because list() carried no link and a trace id was a dead
    end. list() now carries a template of its own, straight to /logs, so the
    blob naming the redirect meant advertising a second shape — the one that
    lands on the /traces forwarder. Deleted rather than corrected: a session
    fact that every page now states for itself does not belong on the
    handshake, and the blob has a size cap to live inside."""
    s = _settings(opik_url="https://dev.comet.com/opik/api")
    out = render_instructions(s)
    template = trace_link_template(s)
    assert template is not None, "the fallback still exists in code"
    assert template not in out


def test_render_omits_the_trace_link_when_opik_is_unconfigured() -> None:
    out = render_instructions(_settings(opik_url=None, comet_url_override=""))
    assert "session/redirect" not in out


def test_render_says_a_dated_issue_report_is_not_the_whole_window() -> None:
    """Diagnostics groups what its last scan saw. Asked for a week and handed
    issues through yesterday, an agent that stops there reports a false
    all-clear for the gap, so the blob sends it on to raw traces."""
    out = render_instructions(_settings())
    assert "Report covers data through" in out
    assert "list('trace'" in out


def test_render_names_every_diagnostics_state() -> None:
    """The blob is delivered as authoritative context, so an undercount here
    teaches the agent that a state it will meet does not exist. Five states
    live in ``diagnostics_state``; all five belong in the sentence."""
    out = render_instructions(_settings())
    for state in (
        "unavailable on this deployment",
        "never enabled for the project",
        "turned off",
        "not scanned recently",
        "enabled and clean",
    ):
        assert state in out


def test_the_blob_names_the_skills_and_describes_none_of_them() -> None:
    """The blob carries the skill *names*; what each one is for is data in
    ``skills_catalog.SKILL_SUMMARIES``, rendered into the ``read_skill`` tool
    description. OPIK-8400 first put one skill's routing prose in the template
    here, which hardcoded for ``opik-compare`` what is a table entry for the
    other eight and spent every session's context on a duplicate.

    So: every bundled skill is named, and no skill's task phrasings are
    written into the template. A new skill needs no edit to this module.
    """
    out = render_instructions(_settings())
    bullet = out.split("- read_skill")[1]
    for name in skill_names():
        assert name in bullet, f"{name} is bundled but the blob does not name it"
    # Scoped to the read_skill bullet, not the whole blob: the read/list bullet
    # routes "what is broken in production" to list('agent_insights_issue', …),
    # which is a route to a TOOL on this connection and belongs there. It only
    # happens to read like opik-diagnose's phrasing. The rule being pinned is
    # narrower — the bullet that introduces the skills does not describe them.
    for phrasing in SKILL_SUMMARIES.values():
        for quoted in re.findall(r'"([^"]+)"', phrasing):
            assert quoted not in bullet, (
                f"{quoted!r} is a task phrasing from SKILL_SUMMARIES and belongs "
                "in the catalog, not in the instructions template"
            )


def test_a_quality_drop_question_routes_to_the_compare_skill() -> None:
    """The route OPIK-8400 owes, asserted where it actually lives.

    Without it an agent asked "why did quality drop" reaches for
    ``list('experiment')``, reads two aggregate means and answers without ever
    naming a case — the failure ``opik-compare`` was built to prevent. The
    ``read_skill`` tool description is on the surface of every session, so
    that is the routing surface; the blob only has to name the skill.
    """
    assert "opik-compare" in skill_names(), "the route cannot point at a skill we do not ship"
    catalog = read_skill_tool_description()
    summary = SKILL_SUMMARIES["opik-compare"]
    for question in (
        "why did quality drop",
        "which cases regressed",
        "compare these two experiments",
        "did my fix work",
    ):
        assert question in summary, f"{question!r} is not routed by the catalog"
        assert question in catalog, f"{question!r} never reaches the advertised description"
    # And it is named in the session blob, so a host that injects instructions
    # knows the skill exists before it ever expands a tool schema.
    assert "opik-compare" in render_instructions(_settings())


def test_the_handshake_advertises_no_address_the_ui_has_retired() -> None:
    """The blob used to name the trace redirect, because list() carried no
    link and a trace id was a dead end. list() carries one now — straight to
    /logs — so naming the redirect meant advertising two shapes and telling
    the agent to prefer the one that lands on /traces, which v2 keeps only to
    forward. The redirect is still the fallback in code for a session that
    cannot name its workspace; it is not something to hand out."""
    blob = render_instructions(
        Settings(
            opik_api_key="k",
            comet_workspace="demo-ws",
            opik_url="https://opik.test/api/",
        )
    )
    assert "session/redirect" not in blob
    assert "/traces" not in blob


def test_the_link_rule_covers_every_row_and_not_just_the_first() -> None:
    """The rule's first version said "never print a bare URL" and was still
    obeyed halfway: an answer that linked the first few rows properly and then
    dropped to raw addresses for the rest, which is the same defect wearing a
    table for a hat. So the rule now names the failure instead of the ideal.
    """
    blob = render_instructions(
        Settings(
            opik_api_key="k",
            comet_workspace="demo-ws",
            opik_url="https://opik.test/api/",
        )
    )
    assert "every row" in blob
    assert "url_opens" in blob
    assert "url_absent" in blob

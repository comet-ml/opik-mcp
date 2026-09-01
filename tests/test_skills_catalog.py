"""The skills catalog — what the MCP serves, and what it refuses (OPIK-7472).

`skills_catalog` is the only runtime reader of the bundled skills tree, feeding
both MCP entry points (`resources/*` and the `read_skill` tool). These tests pin
the three properties the module claims: the listing is deterministic and excludes
what the published pack excludes, content is verbatim, and a URI is resolved by
allow-list lookup so no caller-supplied path can escape the tree.

`skills_ref` — the agentskills.io reference implementation, a dev dependency —
is used as the oracle for skill discovery, so "which directories are skills" is
answered here the same way an agent's own tooling answers it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from skills_ref import validate

from opik_mcp import skills_catalog as catalog

SKILLS_SRC = Path(__file__).resolve().parent.parent / "src" / "opik_mcp" / "skills"


# --- listing ------------------------------------------------------------- #


def test_catalog_is_not_empty() -> None:
    """An empty catalog would serve a valid-looking but useless surface: the tool
    description would list no skills and `resources/list` would return nothing."""
    assert catalog.iter_skill_files(), "no skills discovered — the wheel is broken"
    assert catalog.skill_names()


def test_listing_is_deterministic_and_entry_point_first() -> None:
    """Hosts cache `resources/list`; the MCP spec expects a deterministic listing.
    A dict/set-ordering regression would reshuffle the list per process."""
    files = catalog.iter_skill_files()
    expected = sorted(files, key=lambda f: (f.skill, not f.is_entry_point, f.path))
    assert list(files) == expected
    for skill in catalog.skill_names():
        first = next(f for f in files if f.skill == skill)
        assert first.is_entry_point, f"{skill} does not lead with its SKILL.md"


def test_every_skill_directory_is_discovered() -> None:
    """Cross-checked against `skills_ref`, not against a hardcoded list: a skill
    added to the tree must appear over MCP without anyone editing this test."""
    on_disk = {d.name for d in SKILLS_SRC.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()}
    assert set(catalog.skill_names()) == on_disk
    for name in catalog.skill_names():
        assert not validate(SKILLS_SRC / name), f"{name} fails the agentskills.io spec"


def test_excluded_directories_are_never_served() -> None:
    """`evals/` is tens of KB of fixtures per skill that no agent reads, and the
    published pack drops it. Serving it over MCP would make the two disagree."""
    served = [f.uri for f in catalog.iter_skill_files()]
    for excluded in catalog.EXCLUDED_DIRS:
        leaked = [u for u in served if f"/{excluded}/" in u]
        assert not leaked, f"{excluded}/ leaked into the catalog: {leaked}"
    assert not [u for u in served if "/." in u], "a dotfile leaked into the catalog"
    # The exclusions must actually be doing work — if `evals/` disappeared from
    # the source tree this test would otherwise pass vacuously forever.
    assert any(d.is_dir() for d in SKILLS_SRC.glob("*/evals")), (
        "no evals/ directory in the source tree; this test no longer proves anything"
    )


def test_every_served_file_is_readable_utf8() -> None:
    """Everything served goes out over a text transport. A binary file reaching
    the tree should fail here rather than in an agent's context window."""
    for entry in catalog.iter_skill_files():
        assert catalog.read_skill_file(entry), f"{entry.uri} is empty"


def test_content_is_verbatim() -> None:
    """Byte-for-byte with the packaged file: an agent reading a skill over MCP and
    a user installing the pack must be reading the same document."""
    for entry in catalog.iter_skill_files():
        on_disk = (SKILLS_SRC / entry.skill / entry.path).read_text(encoding="utf-8")
        assert catalog.read_skill_file(entry) == on_disk


def test_mime_types_are_markdown_for_skill_documents() -> None:
    for entry in catalog.iter_skill_files():
        if entry.path.endswith(".md"):
            assert entry.mime_type == "text/markdown"


# --- the tool description ------------------------------------------------ #


def test_every_bundled_skill_has_a_summary() -> None:
    """The summaries are what tells a calling agent a skill exists. A skill added
    without one is a skill the agent is never told about, which is worse than a
    missing skill: the tool advertises a catalog that is quietly incomplete."""
    assert set(catalog.SKILL_SUMMARIES) == set(catalog.skill_names()), (
        "SKILL_SUMMARIES and the bundled skills disagree — add a one-line summary "
        "for a new skill (or drop the stale entry)"
    )


def test_summaries_stay_brief() -> None:
    """These lines are charged to every session's context. Long enough to route on,
    short enough not to matter — the frontmatter description is the long form."""
    for name, summary in catalog.SKILL_SUMMARIES.items():
        assert 0 < len(summary) <= 170, f"{name}'s summary is {len(summary)} chars"


def test_tool_description_lists_every_skill() -> None:
    description = catalog.read_skill_tool_description()
    for name in catalog.skill_names():
        assert name in description, f"{name} missing from the read_skill description"


def test_tool_description_gates_on_context_not_on_installing_anything() -> None:
    """The question at call time is whether the skill is already in the agent's
    context. Telling it to install a pack is advice for a person, not a step the
    agent can take mid-task, and it was charged to every session."""
    description = catalog.read_skill_tool_description().lower()
    assert "context" in description
    assert "npx" not in description
    assert "install" not in description


def test_tool_description_documents_every_accepted_form() -> None:
    """`skill_name` takes a name, a path, or a resource URI. A form the tool accepts
    but never advertises is a form no agent uses."""
    description = catalog.read_skill_tool_description()
    assert "`opik`" in description
    assert "opik/references/tracing-python.md" in description
    assert catalog.SKILLS_URI_PREFIX in description


def test_tool_description_lists_every_readable_path() -> None:
    """The inventory is the expensive part of this description — every path is
    charged to every session — and it is there so a caller can fetch a reference
    directly instead of reading a 5 KB SKILL.md to learn its name. A path that
    exists but is unlisted is a document no agent will ask for."""
    description = catalog.read_skill_tool_description()
    for entry in catalog.iter_skill_files():
        assert entry.path in description, f"{entry.skill}/{entry.path} is not advertised"
    for name in catalog.skill_names():
        assert f"- {name}: SKILL.md" in description


def test_tool_description_stays_within_a_sane_budget() -> None:
    """It ships on every tools/list, so growth should be a decision, not a drift.
    The inventory is ~20 paths today; a jump means a skill grew a large reference
    tree and the inventory may need summarising instead of enumerating."""
    length = len(catalog.read_skill_tool_description())
    assert length <= 4000, f"read_skill description is {length} chars — is the inventory too big?"


# NOTE: there is deliberately no test tying these summaries to the authored
# frontmatter. They are MCP-only copy and are meant to diverge where the
# frontmatter's phrasing is not what a user would say — `opik`'s "what span types
# exist" being the case in point. Two attempts at a drift guard were removed
# rather than kept: requiring verbatim quotes forbids exactly the divergence that
# is the point, and requiring shared vocabulary only passed by accident (the
# phrase "why aren't my traces showing up" matched because "trace" is a substring
# of "OpikTracer"). A check that passes for the wrong reason is worse than none —
# it reads as coverage while proving nothing. Whether these lines route well is a
# review question, and ultimately an eval question: `evals/` already exists under
# two of the skills, and the `skill` / `had_reference` BI labels on `read_skill`
# show which skills actually get fetched.


def test_summaries_are_task_shaped_not_a_table_of_contents() -> None:
    """Every summary is keyed on the kind of task the user gave — a quoted task
    phrasing, or (for `opik-evaluate`, whose trigger reads naturally as prose) a
    statement of what the user wants. The agent is matching a request it already
    has in hand, so a line that only lists a skill's contents leaves it an
    inference to make, which is the thing this list exists to avoid."""
    for name, summary in catalog.SKILL_SUMMARIES.items():
        quoted = re.findall(r'"([^"]+)"', summary)
        assert quoted or "the user wants" in summary, (
            f"{name}'s summary is not keyed on a task — give it a quoted task "
            "phrasing, not a description of the skill's contents"
        )


# --- resolution ---------------------------------------------------------- #


def test_a_skill_name_alone_resolves_to_the_skill_itself() -> None:
    entry = catalog.resolve("opik")
    assert entry.skill == "opik"
    assert entry.path == "SKILL.md"


@pytest.mark.parametrize(
    "form",
    [
        "opik/references/observability.md",
        "opik://skills/opik/references/observability.md",
        "../opik/references/observability.md",
        "opik/observability",
        "  /opik/references/observability.md  ",
    ],
)
def test_every_documented_form_reaches_the_same_document(form: str) -> None:
    """One argument, several forms, because an agent arrives holding whichever one
    it last saw: a path from a SKILL.md or from this tool's own output, a URI from
    `resources/list`, or the `../` form a SKILL.md uses to cite a sibling skill."""
    assert catalog.resolve(form) == catalog.resolve("opik/references/observability.md")


def test_readable_paths_lead_with_the_entry_point() -> None:
    paths = catalog.readable_paths("opik")
    assert paths[0] == "SKILL.md"
    assert "references/tracing-python.md" in paths
    assert catalog.readable_paths("opik-explain") == ("SKILL.md",)


@pytest.mark.parametrize(
    ("shape", "requested"),
    [
        ("name", "opik"),
        ("path", "opik/references/observability.md"),
        ("path", "../opik/references/observability.md"),
        ("uri", "opik://skills/opik/SKILL.md"),
    ],
)
def test_request_shape_classifies_the_form(shape: str, requested: str) -> None:
    """The BI label and the resolver read the same argument the same way — a
    `uri` in the data means a caller browsed `resources/list` first."""
    assert catalog.request_shape(requested) == shape


@pytest.mark.parametrize(
    "requested",
    [
        "",
        "   ",
        "nope",
        "instrument",
        "opik/references/does-not-exist.md",
        "opik/evals/metrics.py",
        "opik://skills/opik/evals/metrics.py",
        "opik://traces/some-uuid",
        # Nothing outside the tree is reachable: every form ends in a lookup over
        # the enumerated set, never a path join, so traversal misses rather than
        # escaping. Pinned because the failure mode — serving arbitrary files off
        # the server's disk to any connected agent — is severe.
        "opik/../../../etc/passwd",
        "opik/../../scripts/build_skills_pack.py",
        "/etc/passwd",
        "../../../../etc/passwd",
        "opik://skills/../../../etc/passwd",
        "opik/references/../../../pyproject.toml",
    ],
)
def test_unknown_requests_raise_unknown_skill(requested: str) -> None:
    with pytest.raises(catalog.UnknownSkillError):
        catalog.resolve(requested)


def test_error_messages_name_the_alternatives() -> None:
    """A wrong guess should cost one turn. An error that just says "not found"
    sends the agent fishing."""
    with pytest.raises(catalog.UnknownSkillError, match="opik-instrument"):
        catalog.resolve("instrument")
    # A bad path inside a real skill lists that skill's readable paths, in the
    # form the caller should pass them.
    expected = re.escape("opik/references/tracing-python.md")
    with pytest.raises(catalog.UnknownSkillError, match=expected):
        catalog.resolve("opik/references/nope.md")


def test_unknown_skill_error_buckets_as_validation() -> None:
    """`analytics/errors.py` reads `error_kind` off the class; a caller naming a
    skill that doesn't exist is a payload problem, not an outage to page on."""
    assert catalog.UnknownSkillError.error_kind == "validation"
    assert catalog.UnknownSkillError.http_status == 400


# --- the tool body ------------------------------------------------------- #


def test_run_read_skill_returns_header_then_verbatim_content() -> None:
    out = catalog.run_read_skill("opik-instrument")
    header, _, body = out.partition("\n\n")
    assert header.startswith("[read_skill: opik-instrument path=SKILL.md bytes=")
    on_disk = (SKILLS_SRC / "opik-instrument" / "SKILL.md").read_text(encoding="utf-8")
    assert on_disk in body


def test_run_read_skill_lists_reference_paths_for_a_skill() -> None:
    """A SKILL.md routinely tells the agent to go read a reference; without the
    footer it has to guess the argument for the follow-up call."""
    footer = catalog.run_read_skill("opik").rsplit("References for this skill", 1)[-1]
    for path in catalog.readable_paths("opik")[1:]:
        assert f"- opik/{path}" in footer
    # And the footer shows the exact call, so the follow-up needs no guesswork.
    assert 'read_skill("opik/references/' in footer


def test_run_read_skill_fetches_one_reference_and_omits_the_footer() -> None:
    out = catalog.run_read_skill("opik/references/observability.md")
    assert out.startswith("[read_skill: opik path=references/observability.md bytes=")
    on_disk = (SKILLS_SRC / "opik" / "references" / "observability.md").read_text(encoding="utf-8")
    assert on_disk in out
    assert "References for this skill" not in out


def test_the_header_quotes_the_resolved_file_not_the_callers_spelling() -> None:
    """Output is documentation an agent imitates. A caller that passed a bare
    reference name or a URI should see the canonical path echoed back, so its next
    call uses the form the tool advertises."""
    for form in ("opik/observability", "opik://skills/opik/references/observability.md"):
        assert catalog.run_read_skill(form).startswith(
            "[read_skill: opik path=references/observability.md bytes="
        )


def test_a_single_document_skill_gets_no_reference_footer() -> None:
    assert "References for this skill" not in catalog.run_read_skill("opik-explain")


def test_cache_metadata_is_a_public_scope_and_a_positive_ttl() -> None:
    """Skill content is identical for every caller and carries no workspace data,
    which is exactly what licenses a shared (`public`) cache."""
    assert catalog.SKILLS_CACHE_SCOPE == "public"
    assert catalog.SKILLS_TTL_MS > 0


def test_reading_a_hand_built_entry_outside_the_tree_is_refused() -> None:
    """`read_skill_file` is the one function that joins path segments, so it
    re-checks its argument against the enumerated set. Without that, a caller
    constructing a `SkillFile` directly could read any file on the server's disk —
    the severe end of this module's failure modes."""
    forged = catalog.SkillFile(skill="opik", path="../../../pyproject.toml")
    with pytest.raises(catalog.UnknownSkillError):
        catalog.read_skill_file(forged)


# --- error messages: recoverable in one turn ------------------------------ #
#
# A wrong guess should cost one turn. These pin the property that matters — the
# error names what the caller should have asked for — rather than exact wording,
# which is free to improve.


@pytest.mark.parametrize(
    "requested",
    ["instrument", "the opik tracing skill please", "opik://skills/opk/SKILL.md"],
)
def test_a_bad_skill_name_is_answered_with_every_skill_name(requested: str) -> None:
    with pytest.raises(catalog.UnknownSkillError) as raised:
        catalog.resolve(requested)
    for name in catalog.skill_names():
        assert name in str(raised.value)


@pytest.mark.parametrize(
    "requested",
    [
        "opik/references/tracig-python.md",
        "opik/tracing",
        "opik/../../../etc/passwd",
    ],
)
def test_a_bad_document_is_answered_with_that_skills_documents(requested: str) -> None:
    """Naming the whole skill list here would be useless — the caller already found
    the right skill and missed the document inside it."""
    with pytest.raises(catalog.UnknownSkillError) as raised:
        catalog.resolve(requested)
    message = str(raised.value)
    for path in catalog.readable_paths("opik"):
        assert f"opik/{path}" in message


def test_a_bad_document_in_uri_form_is_answered_with_uris() -> None:
    """The regression this guards: a URI used to fall down a separate road and get
    the skill list back, so the same mistake was recoverable in path form and a
    dead end in URI form. An agent holding URIs must be handed URIs to retry with —
    the translation back is the step that costs the extra turn."""
    with pytest.raises(catalog.UnknownSkillError) as raised:
        catalog.resolve("opik://skills/opik/references/nope.md")
    message = str(raised.value)
    for path in catalog.readable_paths("opik"):
        assert f"{catalog.SKILLS_URI_PREFIX}opik/{path}" in message


def test_an_unknown_resource_uri_message_points_somewhere() -> None:
    """The resource surface's own miss message. FastMCP's default is
    "Unknown resource: <uri>" — accurate and useless."""
    known_skill = catalog.unknown_skill_uri_message(f"{catalog.SKILLS_URI_PREFIX}opik/nope.md")
    assert f"{catalog.SKILLS_URI_PREFIX}opik/SKILL.md" in known_skill

    unknown_skill = catalog.unknown_skill_uri_message(f"{catalog.SKILLS_URI_PREFIX}opk/SKILL.md")
    assert "resources/list" in unknown_skill
    for name in catalog.skill_names():
        assert name in unknown_skill

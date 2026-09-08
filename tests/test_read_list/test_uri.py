"""``opik://`` URI parsing — covers the six singleton shapes."""

from __future__ import annotations

import pytest

from opik_mcp.read_list.uri import (
    InvalidURI,
    ParsedURI,
    looks_like_opik_link,
    looks_like_thread_url,
    looks_like_uri,
    parse,
)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("opik://projects/p-1", ParsedURI("project", "p-1")),
        ("opik://traces/tr-1", ParsedURI("trace", "tr-1")),
        ("opik://spans/sp-1", ParsedURI("span", "sp-1")),
        ("opik://test-suites/ds-1", ParsedURI("test_suite", "ds-1")),
        ("opik://experiments/ex-1", ParsedURI("experiment", "ex-1")),
        ("opik://prompts/pr-1", ParsedURI("prompt", "pr-1")),
    ],
)
def test_parse_singleton_shapes(uri: str, expected: ParsedURI) -> None:
    assert parse(uri) == expected


def test_looks_like_uri_only_matches_opik_prefix() -> None:
    assert looks_like_uri("opik://projects/p-1")
    assert not looks_like_uri("p-1")
    assert not looks_like_uri("http://opik.test/x")


def test_parse_rejects_unknown_entity() -> None:
    with pytest.raises(InvalidURI):
        parse("opik://datasets/d-1")


def test_parse_rejects_collection_paths() -> None:
    """Bare ``opik://projects`` is a list URI — not valid for read input."""
    with pytest.raises(InvalidURI):
        parse("opik://projects")
    with pytest.raises(InvalidURI):
        parse("opik://projects/p-1/traces")


def test_parse_underscore_form_for_test_suite_rejected() -> None:
    """We canonicalize on hyphens in the URI shape to match the old resources.py."""
    with pytest.raises(InvalidURI):
        parse("opik://test_suites/ds-1")


# --- threads -------------------------------------------------------------- #


def test_parse_canonical_thread_uri_carries_project() -> None:
    assert parse("opik://projects/p-9/threads/th-1") == ParsedURI(
        "thread", "th-1", project_id="p-9"
    )


def test_parse_web_thread_url_extracts_project_and_thread() -> None:
    url = "https://app.opik.test/my-ws/projects/p-9/traces?page=1&thread=th-1&x=2"
    assert parse(url) == ParsedURI("thread", "th-1", project_id="p-9")


def test_parse_web_thread_url_url_decodes_thread_id() -> None:
    url = "https://app.opik.test/ws/projects/p-9/traces?thread=conv%2F42"
    assert parse(url) == ParsedURI("thread", "conv/42", project_id="p-9")


def test_looks_like_thread_url() -> None:
    assert looks_like_thread_url("https://x.test/ws/projects/p/traces?thread=t")
    assert not looks_like_thread_url("https://x.test/ws/projects/p/traces")  # no thread=
    assert not looks_like_thread_url("https://x.test/ws/datasets?thread=t")  # no /projects/
    assert not looks_like_thread_url("opik://projects/p/threads/t")  # not http(s)
    # 'thread=' is a substring of 'other_thread=' but must NOT be treated as a
    # thread link — the gate uses the same anchored [?&]thread= regex as parse.
    assert not looks_like_thread_url("https://x.test/ws/projects/p/traces?other_thread=t")


def test_thread_uri_not_confused_with_project_singleton() -> None:
    # A plain project singleton must still parse as project, not thread.
    assert parse("opik://projects/p-1") == ParsedURI("project", "p-1")


# --- Diagnostics (agent insights) issues ---------------------------------- #


def test_parse_canonical_issue_uri_carries_project() -> None:
    assert parse("opik://projects/p-9/agent-insights-issues/is-1") == ParsedURI(
        "agent_insights_issue", "is-1", project_id="p-9"
    )


def test_parse_web_diagnostics_link_extracts_project_and_issue() -> None:
    url = "https://www.comet.com/opik/my-ws/projects/p-9/diagnostics?issue=is-1&tab=open"
    assert parse(url) == ParsedURI("agent_insights_issue", "is-1", project_id="p-9")


def test_parse_web_diagnostics_resolved_link_too() -> None:
    url = "https://www.comet.com/opik/my-ws/projects/p-9/diagnostics/resolved?issue=is-2"
    assert parse(url) == ParsedURI("agent_insights_issue", "is-2", project_id="p-9")


def test_parse_web_issue_link_url_decodes_issue_id() -> None:
    url = "https://x.test/ws/projects/p-9/diagnostics?issue=is%2F42"
    assert parse(url) == ParsedURI("agent_insights_issue", "is/42", project_id="p-9")


def test_looks_like_opik_link_covers_thread_and_issue_links() -> None:
    assert looks_like_opik_link("https://x.test/ws/projects/p/traces?thread=t")
    assert looks_like_opik_link("https://x.test/ws/projects/p/diagnostics?issue=i")
    assert not looks_like_opik_link("https://x.test/ws/projects/p/diagnostics")  # no issue=
    assert not looks_like_opik_link("https://x.test/ws/diagnostics?issue=i")  # no /projects/
    assert not looks_like_opik_link("opik://projects/p/agent-insights-issues/i")  # not http
    # 'issue=' inside another key is not an issue link.
    assert not looks_like_opik_link("https://x.test/ws/projects/p/diagnostics?other_issue=i")


def test_thread_query_wins_when_both_present() -> None:
    """A URL carrying both keys is a thread link with an unrelated issue param;
    the thread rule is checked first so existing behaviour is unchanged."""
    url = "https://x.test/ws/projects/p-9/traces?thread=th-1&issue=is-1"
    assert parse(url) == ParsedURI("thread", "th-1", project_id="p-9")


def test_issue_uri_not_confused_with_thread_or_project() -> None:
    assert parse("opik://projects/p-9/threads/th-1").entity_type == "thread"
    assert parse("opik://projects/p-9").entity_type == "project"
    with pytest.raises(InvalidURI):
        parse("opik://projects/p-9/agent-insights-issues")  # collection, not a singleton

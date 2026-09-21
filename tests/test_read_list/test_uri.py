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
        ("opik://datasets/ds-1", ParsedURI("dataset", "ds-1")),
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


def test_parse_legacy_test_suites_form_still_resolves_to_dataset() -> None:
    """The pre-rename spelling keeps working — old links stay resolvable."""
    assert parse("opik://test-suites/ds-1") == ParsedURI("dataset", "ds-1")


def test_parse_rejects_unknown_entity() -> None:
    with pytest.raises(InvalidURI):
        parse("opik://widgets/w-1")


def test_parse_rejects_collection_paths() -> None:
    """Bare ``opik://projects`` is a list URI — not valid for read input."""
    with pytest.raises(InvalidURI):
        parse("opik://projects")
    with pytest.raises(InvalidURI):
        parse("opik://projects/p-1/traces")


def test_parse_underscore_form_of_legacy_spelling_rejected() -> None:
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


# --- pasted links for the two entities people share ----------------------- #

_COMPARE = (
    "https://www.comet.com/opik/ws/experiments/019f8d97-c83c-7597-b40a-bd2e0e1ad558"
    "/compare?experiments=%5B%22019f8d9c-9bcb-7ad5-8b1a-2f00b87a4222%22%5D"
)


def test_a_pasted_compare_link_resolves_to_its_experiment() -> None:
    """What a user pastes when they say "here's my experiment". It used to
    come back as "Verify the ID is a valid UUID" over a URL carrying two."""
    assert parse(_COMPARE) == ParsedURI("experiment", "019f8d9c-9bcb-7ad5-8b1a-2f00b87a4222")


def test_a_compare_link_of_two_runs_opens_the_baseline() -> None:
    """The route lists the baseline first, so the read lands on the run the
    comparison is against rather than the candidate."""
    url = (
        "https://www.comet.com/opik/ws/experiments/ds-1/compare"
        "?experiments=%5B%22base-1%22%2C%22cand-2%22%5D"
    )
    assert parse(url) == ParsedURI("experiment", "base-1")


def test_a_project_scoped_compare_link_resolves_the_same_way() -> None:
    """The UI has both a workspace-level and a project-level compare route."""
    url = "https://opik.test/ws/projects/p-1/experiments/ds-1/compare?experiments=%5B%22e-9%22%5D"
    assert parse(url) == ParsedURI("experiment", "e-9")


def test_a_trace_deep_link_resolves_to_the_trace() -> None:
    """``tls_trace`` is how the UI opens one trace inside an experiment's logs
    tab; ``trace_id`` is what this server's own redirect link carries, so a
    user pasting back what we handed them lands on the same read."""
    tls = "https://opik.test/ws/projects/p-1/experiments/ds-1/compare?tab=logs&tls_trace=tr-7"
    assert parse(tls) == ParsedURI("trace", "tr-7")
    redirect = "https://opik.test/api/v1/session/redirect/projects/?trace_id=tr-8&path=aHR0cA"
    assert parse(redirect) == ParsedURI("trace", "tr-8")


def test_a_trace_link_wins_over_the_compare_view_it_sits_on() -> None:
    """Both patterns match that URL; the one naming a single record is the
    one the user is looking at."""
    url = (
        "https://opik.test/ws/experiments/ds-1/compare"
        "?experiments=%5B%22e-1%22%5D&tab=logs&tls_trace=tr-3"
    )
    assert parse(url) == ParsedURI("trace", "tr-3")


def test_an_unrelated_http_url_is_not_claimed() -> None:
    """A plain URL falls through to raw-id handling rather than being parsed
    into some entity it never named."""
    assert not looks_like_opik_link("https://example.com/experiments/x/compare")
    assert not looks_like_opik_link("https://example.com/page?experiments=notjson")


def test_a_compare_link_with_an_unparseable_run_list_is_not_claimed() -> None:
    """The gate uses the regex parse extracts with, and extraction has to
    survive whatever sits in the query string."""
    assert not looks_like_opik_link(
        "https://opik.test/ws/experiments/ds/compare?experiments=%5B%5D"
    )

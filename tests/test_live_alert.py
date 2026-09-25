"""The Slack alert a failed live run posts: what it tells the person who reads it.

The alert is the whole interface of the nightly for most people: they see it,
not the run. So it has to say what failed and where, name the likely cause,
and say what to do next, with links. These check that it does, for each way a
run can fail, from the junit file to the message.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from scripts.live_alert import Job, Report, Run, build_message, read_report

RUN = Run(
    repo="comet-ml/opik-mcp",
    sha="0a7a2c6f00d5b2b1a0c4f4e6a1b2c3d4e5f60718",
    ref="main",
    event="schedule",
    run_url="https://github.com/comet-ml/opik-mcp/actions/runs/42",
    commit_title="[OPIK-8490] Alert on timed-out nightlies",
    last_green_sha="1f2e3d4c5b6a79880a1b2c3d4e5f60718293a4b5",
    commits_since_green=3,
)
UNCHANGED = replace(RUN, last_green_sha=RUN.sha, commits_since_green=0)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="6" failures="1" errors="1" skipped="2">
<testcase classname="tests.live.test_traces" name="test_a_window_returns_exactly_the_traces"/>
<testcase classname="tests.live.test_traces" name="test_an_error_filter_returns_the_errored_traces">
<failure message="AssertionError: the error filter returned 11 traces, not 12&#10;assert 11 == 12"
>tb</failure>
</testcase>
<testcase classname="tests.live.test_writes" name="test_a_score_lands_on_its_trace">
<error message="failed on setup with &quot;RuntimeError: could not seed&quot;">tb</error>
</testcase>
<testcase classname="tests.live.test_sizes" name="test_skipped">
<skipped message="no ollie"/></testcase>
<testcase classname="tests.live.test_sizes" name="test_other"/>
<testcase classname="tests.live.test_sizes" name="test_over_the_cap">
<skipped type="pytest.xfail" message="a whole record above the host's cap"/></testcase>
</testsuite></testsuites>"""

FAILED = "test_an_error_filter_returns_the_errored_traces"


def _report(tmp_path: Path) -> Report:
    path = tmp_path / "live.xml"
    path.write_text(JUNIT)
    report = read_report(path)
    assert report is not None
    return report


def _job(key: str, result: str, report: Report | None) -> Job:
    return Job(
        key=key,
        result=result,
        version="2.2.80",
        report=report,
        log_url=f"https://github.com/comet-ml/opik-mcp/actions/runs/42/job/{key}",
    )


def _text(message: dict[str, object]) -> str:
    return json.dumps(message)


def _section(message: dict[str, object], heading: str) -> str:
    """The text of the one section that starts with ``heading``."""
    texts = [
        block["text"]["text"]
        for block in json.loads(json.dumps(message))["attachments"][0]["blocks"]
        if block.get("type") == "section" and "text" in block
    ]
    found = [text for text in texts if text.startswith(f"*{heading}*")]
    assert len(found) == 1, f"no single {heading!r} section in {texts}"
    return str(found[0])


def test_a_junit_file_becomes_counts_and_each_failure_with_its_message(tmp_path: Path) -> None:
    report = _report(tmp_path)
    # A known failure (strict xfail) is neither a skip nor news.
    assert (report.passed, report.failed, report.skipped) == (2, 2, 1)
    assert [(f.test, f.message) for f in report.failures] == [
        (FAILED, "the error filter returned 11 traces, not 12"),
        ("test_a_score_lands_on_its_trace", 'failed on setup with "RuntimeError: could not seed"'),
    ]


def test_a_missing_junit_file_is_a_run_that_never_reached_the_tests(tmp_path: Path) -> None:
    assert read_report(tmp_path / "absent.xml") is None


def test_the_alert_names_each_failure_once_with_its_file_and_reason(tmp_path: Path) -> None:
    report = _report(tmp_path)
    message = build_message(
        RUN, [_job("live-local", "failure", report), _job("live-prod", "failure", report)]
    )
    listed = _section(message, "2 failing tests")
    file_link = f"https://github.com/comet-ml/opik-mcp/blob/{RUN.sha}/tests/live/test_traces.py"
    assert (listed.count(FAILED), file_link in listed) == (1, True)
    assert "The error filter returned 11 traces, not 12. Fails on both backends." in listed


def test_a_failure_on_both_backends_points_at_opik_mcp(tmp_path: Path) -> None:
    report = _report(tmp_path)
    text = _text(
        build_message(
            RUN, [_job("live-local", "failure", report), _job("live-prod", "failure", report)]
        )
    )
    assert "change in opik-mcp" in text


def test_a_failure_links_the_commits_since_the_last_green_run(tmp_path: Path) -> None:
    text = _text(build_message(RUN, [_job("live-local", "failure", _report(tmp_path))]))
    compare = f"https://github.com/comet-ml/opik-mcp/compare/{RUN.last_green_sha}...{RUN.sha}"
    assert (compare in text, "3 commits since the last green run" in text) == (True, True)


def test_a_failure_with_no_change_since_the_last_green_run_points_outside_opik_mcp(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    jobs = [_job("live-local", "failure", report), _job("live-prod", "failure", report)]
    text = _text(build_message(UNCHANGED, jobs))
    assert ("nothing in opik-mcp changed" in text, "change in opik-mcp" in text) == (True, False)


def test_a_failure_only_on_cloud_says_how_to_rerun_against_cloud(tmp_path: Path) -> None:
    jobs = [_job("live-local", "success", None), _job("live-prod", "failure", _report(tmp_path))]
    text = _text(build_message(RUN, jobs))
    assert ("only on Opik cloud" in text, "OPIK_URL=https://www.comet.com/opik/api" in text) == (
        True,
        True,
    )


def test_the_alert_reads_as_sentences_with_no_separator_dots(tmp_path: Path) -> None:
    report = _report(tmp_path)
    text = _text(
        build_message(
            RUN, [_job("live-local", "failure", report), _job("live-prod", "failure", report)]
        )
    )
    # A middle dot, an em dash and an en dash: separators, not sentences.
    assert [mark for mark in ("\u00b7", "\u2014", "\u2013") if mark in text] == []


def test_a_failure_only_on_open_source_points_at_a_new_opik_release(tmp_path: Path) -> None:
    jobs = [_job("live-local", "failure", _report(tmp_path)), _job("live-prod", "success", None)]
    text = _text(build_message(RUN, jobs))
    assert ("new Opik release" in text, "opik_version" in text) == (True, True)


def test_a_job_that_never_reached_its_tests_says_so() -> None:
    text = _text(build_message(RUN, [_job("live-local", "failure", None)]))
    assert "before any test ran" in text


def test_a_timed_out_job_says_so() -> None:
    text = _text(build_message(RUN, [_job("live-prod", "cancelled", None)]))
    assert "timed out" in text


def test_the_alert_gives_a_command_that_reruns_exactly_the_failing_tests(tmp_path: Path) -> None:
    text = _text(build_message(RUN, [_job("live-local", "failure", _report(tmp_path))]))
    assert f"-k '{FAILED} or test_a_score_lands_on_its_trace'" in text


def test_the_alert_links_the_run_the_commit_and_the_failing_job_log(tmp_path: Path) -> None:
    text = _text(build_message(RUN, [_job("live-local", "failure", _report(tmp_path))]))
    links = [
        RUN.run_url,
        f"https://github.com/comet-ml/opik-mcp/commit/{RUN.sha}",
        "https://github.com/comet-ml/opik-mcp/actions/runs/42/job/live-local",
    ]
    assert [link for link in links if link not in text] == []

"""The Slack alert for a failed live run, and the test summary it is built from.

Most people only ever see the alert, not the run, so it has to answer what
they ask when it arrives: what failed and where, which tests and why, the
likely cause, and what to do next, with a link for each step. The message is
built by a pure function (``build_message``) that ``tests/test_live_alert.py``
checks case by case; ``main`` only reads the environment and posts.

Standard library only: the notify job runs it with the runner's ``python3``
and installs nothing.

Commands:

- ``report <junit.xml>``: one line of JSON summing up a test run, for a job
  output. Prints ``null`` when the file is missing: the job never reached the
  tests.
- ``notify``: build the message from the environment and post it to
  ``SLACK_WEBHOOK_URL``. Without a webhook it prints a notice and succeeds,
  so a fork or a fresh repo never goes red over Slack.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

#: The Live workflow's page, where a run can be started by hand.
WORKFLOW = "live.yaml"
#: How many failing tests the message names before it points at the run.
MAX_NAMED = 10
#: Slack refuses a section text above 3,000 characters.
_SECTION_LIMIT = 2_900

_LABELS = {"live-local": "Open source Opik", "live-prod": "Opik cloud"}
_SHORT = {"live-local": "open source", "live-prod": "cloud"}
_TRIGGERS = {
    "schedule": "Nightly run",
    "workflow_dispatch": "Manual run",
    "push": "Push",
    "pull_request": "Pull request",
}


# --- the test summary -----------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    module: str
    test: str
    message: str


@dataclass(frozen=True)
class Report:
    passed: int
    failed: int
    skipped: int
    failures: tuple[Failure, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> Report | None:
        data: object = json.loads(raw) if raw.strip() else None
        if not isinstance(data, dict):
            return None
        failures = data.get("failures")
        return cls(
            passed=int(data.get("passed", 0)),
            failed=int(data.get("failed", 0)),
            skipped=int(data.get("skipped", 0)),
            failures=tuple(
                Failure(str(f.get("module", "")), str(f.get("test", "")), str(f.get("message", "")))
                for f in (failures if isinstance(failures, list) else [])
                if isinstance(f, dict)
            ),
        )


def _plain(message: str) -> str:
    """An assertion message without the exception class pytest puts in front."""
    return re.sub(r"^(AssertionError|Failed): ", "", message)


def read_report(path: Path) -> Report | None:
    """What a pytest junit file says, or None when the job never wrote one."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    failures: list[Failure] = []
    total = skipped = known = 0
    for case in root.iter("testcase"):
        total += 1
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is not None:
            first = (problem.get("message") or "").strip().splitlines()
            failures.append(
                Failure(
                    module=case.get("classname") or "",
                    test=case.get("name") or "",
                    message=_plain(first[0] if first else "")[:200],
                )
            )
        elif (skip := case.find("skipped")) is not None and skip.get("type") != "pytest.xfail":
            # An expected failure (strict xfail) is a known state, not a skip.
            skipped += 1
        elif skip is not None:
            known += 1
    return Report(
        passed=total - len(failures) - skipped - known,
        failed=len(failures),
        skipped=skipped,
        failures=tuple(failures),
    )


# --- the message ------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    repo: str
    sha: str
    ref: str
    event: str
    run_url: str
    commit_title: str
    #: The commit of the last successful Live run on this branch, when known.
    last_green_sha: str | None = None
    #: How many commits landed after it, when known.
    commits_since_green: int | None = None


@dataclass(frozen=True)
class Job:
    #: ``live-local`` or ``live-prod``.
    key: str
    #: The job's result as GitHub reports it: success, failure, cancelled, skipped.
    result: str
    #: The Opik version the job ran against.
    version: str
    report: Report | None
    log_url: str | None


#: What usually stops a job before its tests, by job.
_SETUP_CAUSES = {
    "live-local": "Usually the Opik backend did not start, or a dependency did not install.",
    "live-prod": "Usually the cloud key is missing or expired, or cloud did not answer.",
}

_LOCAL_RERUN = "OPIK_URL=http://localhost:8080 make live"
_CLOUD_RERUN = (
    "OPIK_URL=https://www.comet.com/opik/api OPIK_API_KEY=<key> "
    "OPIK_WORKSPACE=<workspace> make live"
)


def _esc(text: str) -> str:
    """Slack mrkdwn treats these three as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link(url: str, text: str) -> str:
    return f"<{url}|{_esc(text)}>"


def _is_broken(job: Job) -> bool:
    return job.result in ("failure", "cancelled")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _outcome(job: Job) -> str:
    """How one job went, in a few words."""
    if job.result == "skipped":
        return "Did not run."
    if job.result == "cancelled":
        return "Timed out or was cancelled."
    if job.report is None:
        return "Failed before any test ran." if job.result == "failure" else job.result
    r = job.report
    text = f"{r.failed} failed, {r.passed} passed" if r.failed else f"All {r.passed} passed"
    return f"{text}, {r.skipped} skipped." if r.skipped else f"{text}."


def _failing(jobs: list[Job], key: str) -> set[tuple[str, str]]:
    job = next((j for j in jobs if j.key == key), None)
    if job is None or job.report is None:
        return set()
    return {(f.module, f.test) for f in job.report.failures}


def _compare_url(run: Run) -> str | None:
    if not run.last_green_sha or run.last_green_sha == run.sha:
        return None
    return f"https://github.com/{run.repo}/compare/{run.last_green_sha}...{run.sha}"


def _since_green(run: Run) -> str:
    """The commits since the last green run, as a link, or an empty string."""
    url = _compare_url(run)
    if url is None or not run.commits_since_green:
        return ""
    return _link(url, f"{_plural(run.commits_since_green, 'commit')} since the last green run")


def _causes(run: Run, jobs: list[Job]) -> list[str]:
    """The likely cause, read from which jobs failed, how, and what changed."""
    causes: list[str] = []
    for job in jobs:
        name = f"The {_SHORT.get(job.key, job.key)} job (Opik {job.version})"
        if job.result == "cancelled":
            causes.append(f"{name} timed out or was cancelled.")
        elif job.result == "failure" and job.report is None:
            usual = _SETUP_CAUSES.get(job.key, "Its log shows which step failed.")
            causes.append(f"{name} failed before any test ran. {usual}")
    local, cloud = _failing(jobs, "live-local"), _failing(jobs, "live-prod")
    unchanged = run.last_green_sha == run.sha
    if unchanged and (local or cloud):
        causes.append(
            "The last green run was on this same commit, so nothing in opik-mcp changed and "
            "the cause is on the Opik side, either a new release or a change on cloud."
        )
    elif local & cloud:
        causes.append(
            "The same tests fail on open source and on cloud, so the cause is probably a "
            "change in opik-mcp."
        )
    elif local:
        causes.append(
            "The tests fail only on open source Opik. Either a change in opik-mcp broke "
            "them, or a new Opik release changed the behaviour."
        )
    elif cloud:
        causes.append(
            "The tests fail only on Opik cloud, which points at cloud-only behaviour or "
            "a difference between cloud and the open source release."
        )
    return causes


def _failures(jobs: list[Job]) -> list[tuple[Failure, list[str]]]:
    """Each failing test once, with the jobs it failed in."""
    seen: dict[tuple[str, str], tuple[Failure, list[str]]] = {}
    for job in jobs:
        for failure in job.report.failures if job.report else ():
            entry = seen.setdefault((failure.module, failure.test), (failure, []))
            entry[1].append(job.key)
    return list(seen.values())


def _where(keys: list[str]) -> str:
    if len(keys) > 1:
        return "Fails on both backends."
    return f"Fails only on {_SHORT.get(keys[0], keys[0])}."


def _failure_lines(failures: list[tuple[Failure, list[str]]], run: Run) -> str:
    lines: list[str] = []
    for failure, keys in failures[:MAX_NAMED]:
        path = f"{failure.module.replace('.', '/')}.py"
        url = f"https://github.com/{run.repo}/blob/{run.sha}/{path}"
        line = f"• {_link(url, failure.test)}\n   "
        if failure.message:
            message = failure.message.rstrip(".")
            line += f"{_esc(message[:1].upper() + message[1:])}. "
        line += _where(keys)
        lines.append(line)
    if len(failures) > MAX_NAMED:
        lines.append(
            f"• {len(failures) - MAX_NAMED} more are listed in {_link(run.run_url, 'the run')}."
        )
    text = "\n".join(lines)
    return text if len(text) <= _SECTION_LIMIT else f"{text[:_SECTION_LIMIT]}…"


def _steps(run: Run, jobs: list[Job], failures: list[tuple[Failure, list[str]]]) -> list[str]:
    """What to do next, for this kind of failure."""
    steps: list[str] = []
    broken = [job for job in jobs if _is_broken(job)]
    stopped = next((job for job in broken if job.report is None), None)
    if stopped is not None:
        # No test ran, so the log is the only evidence: it goes first.
        where = (
            _link(stopped.log_url, "the job log")
            if stopped.log_url
            else _link(run.run_url, "the run")
        )
        verb = "stopped in" if stopped.result == "cancelled" else "failed at"
        steps.append(f"Open {where} and find the step it {verb}.")
    since = _since_green(run)
    if since:
        steps.append(f"Look through the {since}.")
    local, cloud = _failing(jobs, "live-local"), _failing(jobs, "live-prod")
    if failures:
        names = " or ".join(dict.fromkeys(f.test for f, _ in failures[:MAX_NAMED]))
        if cloud and not local:
            lead = "Run the failing tests against Opik cloud, with the workspace's key:"
            command = _CLOUD_RERUN
        else:
            design_doc = f"https://github.com/{run.repo}/blob/main/docs/live-e2e/design-doc.md"
            lead = (
                "Run the failing tests against a local Opik. The "
                f"{_link(design_doc, 'design doc')} shows how to start one."
            )
            command = _LOCAL_RERUN
        steps.append(f"{lead}\n```{command} PYTEST_ARGS=\"-k '{names}'\"```")
    if any(job.key == "live-local" and job.result == "failure" for job in broken):
        artifacts = _link(f"{run.run_url}#artifacts", "the run")
        steps.append(f"The Opik backend logs are attached to {artifacts}.")
    if local and not cloud:
        dispatch = f"https://github.com/{run.repo}/actions/workflows/{WORKFLOW}"
        steps.append(
            f"{_link(dispatch, 'Run Live by hand')} with `opik_version` set to the release "
            "before this one. If it passes there, the new Opik release changed the behaviour."
        )
    return steps


def build_message(run: Run, jobs: list[Job]) -> dict[str, object]:
    """The Slack Block Kit message for a run with at least one broken job."""
    trigger = _TRIGGERS.get(run.event, run.event)
    timed_out = all(job.result == "cancelled" for job in jobs if _is_broken(job))
    verb = "timed out" if timed_out else "failed"
    headline = f"🔴 {trigger} of the live tests {verb} on {run.ref}"
    commit_url = f"https://github.com/{run.repo}/commit/{run.sha}"
    title = run.commit_title if len(run.commit_title) <= 80 else f"{run.commit_title[:79]}…"
    commit = f"Commit {_link(commit_url, run.sha[:7])}"
    if title:
        commit += f": {_esc(title)}"
    fields = [
        {
            "type": "mrkdwn",
            "text": f"*{_LABELS.get(job.key, job.key)} {_esc(job.version)}*\n{_outcome(job)}",
        }
        for job in jobs
    ]
    failures = _failures(jobs)
    blocks: list[dict[str, object]] = [
        {"type": "header", "text": {"type": "plain_text", "text": headline}},
        {"type": "section", "text": {"type": "mrkdwn", "text": commit}},
        {"type": "section", "fields": fields},
    ]
    causes = _causes(run, jobs)
    if causes:
        text = "*Likely cause*\n" + " ".join(causes)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    if failures:
        heading = f"*{_plural(len(failures), 'failing test')}*"
        text = f"{heading}\n{_failure_lines(failures, run)}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    steps = _steps(run, jobs, failures)
    if steps:
        numbered = "\n".join(f"{n}. {step}" for n, step in enumerate(steps, start=1))
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Next steps*\n{numbered}"}}
        )
    buttons: list[dict[str, object]] = [
        {"type": "button", "text": {"type": "plain_text", "text": "View run"}, "url": run.run_url}
    ]
    buttons += [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": f"{_SHORT.get(job.key, job.key).capitalize()} log",
            },
            "url": job.log_url,
        }
        for job in jobs
        if _is_broken(job) and job.log_url
    ]
    compare = _compare_url(run)
    if compare:
        buttons.append(
            {"type": "button", "text": {"type": "plain_text", "text": "Changes"}, "url": compare}
        )
    blocks.append({"type": "actions", "elements": buttons})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "The Live check is advisory, so this does not block merges.",
                }
            ],
        }
    )
    return {
        "text": f"{trigger} of the live tests {verb} on {run.ref}",
        "attachments": [{"color": "#d1242f", "blocks": blocks}],
    }


# --- the command line -----------------------------------------------------------


def _github(path: str) -> object:
    """A GitHub API read with the job's token; None when it is not available."""
    token = os.environ.get("GH_TOKEN")
    if not token:
        return None
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed: object = json.load(response)
    except (OSError, ValueError):
        return None
    return parsed


def _job_logs(repo: str, run_id: str) -> dict[str, str]:
    found = _github(f"/repos/{repo}/actions/runs/{run_id}/jobs")
    jobs = found.get("jobs") if isinstance(found, dict) else None
    return {
        str(job["name"]): str(job["html_url"])
        for job in (jobs if isinstance(jobs, list) else [])
        if isinstance(job, dict) and "name" in job and "html_url" in job
    }


def _last_green(repo: str, ref: str, sha: str) -> tuple[str | None, int | None]:
    """The last successful Live run's commit on this branch, and commits since."""
    found = _github(
        f"/repos/{repo}/actions/workflows/{WORKFLOW}/runs?branch={ref}&status=success&per_page=1"
    )
    runs = found.get("workflow_runs") if isinstance(found, dict) else None
    first = runs[0] if isinstance(runs, list) and runs else None
    green = (
        str(first.get("head_sha")) if isinstance(first, dict) and first.get("head_sha") else None
    )
    if green is None or green == sha:
        return green, 0 if green else None
    compared = _github(f"/repos/{repo}/compare/{green}...{sha}")
    ahead = compared.get("ahead_by") if isinstance(compared, dict) else None
    return green, ahead if isinstance(ahead, int) else None


def _commit_title(repo: str, sha: str) -> str:
    found = _github(f"/repos/{repo}/commits/{sha}")
    commit = found.get("commit") if isinstance(found, dict) else None
    message = commit.get("message") if isinstance(commit, dict) else None
    return str(message).splitlines()[0] if message else ""


def notify() -> int:
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        sys.stdout.write(
            "::notice::SLACK_WEBHOOK_URL not configured - Slack notification skipped\n"
        )
        return 0
    env = os.environ
    repo, run_id, sha = env["GITHUB_REPOSITORY"], env["GITHUB_RUN_ID"], env["GITHUB_SHA"]
    green, since = _last_green(repo, env.get("GITHUB_REF_NAME", ""), sha)
    run = Run(
        repo=repo,
        sha=sha,
        ref=env.get("GITHUB_REF_NAME", ""),
        event=env.get("GITHUB_EVENT_NAME", ""),
        run_url=f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}",
        commit_title=_commit_title(repo, sha),
        last_green_sha=green,
        commits_since_green=since,
    )
    logs = _job_logs(repo, run_id)
    jobs = [
        Job(
            key=key,
            result=env.get(f"{prefix}_RESULT", ""),
            version=env.get(f"{prefix}_VERSION", ""),
            report=Report.from_json(env.get(f"{prefix}_REPORT", "")),
            log_url=logs.get(key),
        )
        for key, prefix in (("live-local", "LOCAL"), ("live-prod", "PROD"))
    ]
    body = json.dumps(build_message(run, jobs)).encode()
    request = urllib.request.Request(
        webhook, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
    except OSError as err:
        sys.stdout.write(f"::warning::Slack did not take the message: {err}\n")
        return 1
    sys.stdout.write(
        "Slack notified\n" if status == 200 else f"::warning::Slack answered {status}\n"
    )
    return 0 if status == 200 else 1


def main(argv: list[str]) -> int:
    if argv[:1] == ["report"] and len(argv) == 2:
        report = read_report(Path(argv[1]))
        sys.stdout.write(f"{report.to_json() if report else 'null'}\n")
        return 0
    if argv == ["notify"]:
        return notify()
    sys.stderr.write("usage: live_alert.py report <junit.xml> | notify\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

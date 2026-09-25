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


def read_report(path: Path) -> Report | None:
    """What a pytest junit file says, or None when the job never wrote one."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    failures: list[Failure] = []
    total = skipped = 0
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
                    message=(first[0] if first else "")[:200],
                )
            )
        elif case.find("skipped") is not None:
            skipped += 1
    return Report(
        passed=total - len(failures) - skipped,
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


def _esc(text: str) -> str:
    """Slack mrkdwn treats these three as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link(url: str, text: str) -> str:
    return f"<{url}|{_esc(text)}>"


def _broken(job: Job) -> bool:
    return job.result in ("failure", "cancelled")


def _outcome(job: Job) -> str:
    """One line per job: what it ran against and how it went."""
    if job.result == "skipped":
        return "did not run"
    if job.result == "cancelled":
        return "timed out or was cancelled"
    if job.report is None:
        return "failed before any test ran" if job.result == "failure" else job.result
    r = job.report
    counts = f"{r.failed} failed · {r.passed} passed"
    return f"{counts} · {r.skipped} skipped" if r.skipped else counts


def _causes(jobs: list[Job]) -> list[str]:
    """The likely cause, read from which jobs failed and how."""
    causes: list[str] = []
    for job in jobs:
        where = f"{_LABELS.get(job.key, job.key)} {job.version}".strip()
        if job.result == "cancelled":
            causes.append(
                f"*{where}* timed out or was cancelled; its log shows the step it stopped in."
            )
        elif job.result == "failure" and job.report is None:
            causes.append(
                f"*{where}* failed before any test ran: the backend did not start, "
                "a dependency did not install, or a secret is missing."
            )
    failing = {
        job.key: {(f.module, f.test) for f in job.report.failures}
        for job in jobs
        if job.report is not None and job.report.failures
    }
    local, cloud = failing.get("live-local", set()), failing.get("live-prod", set())
    if local & cloud:
        causes.append(
            "Fails on both backends, so most likely a change in opik-mcp: "
            "look at what merged since the last green run."
        )
    elif local:
        causes.append(
            "Fails only on open source Opik: a change in opik-mcp, or a new Opik release. "
            "If nothing merged since the last green run, suspect the release."
        )
    elif cloud:
        causes.append(
            "Fails only on Opik cloud: cloud-only behaviour, or cloud running a different "
            "version from the open source release."
        )
    return causes


def _failures(jobs: list[Job]) -> list[tuple[Failure, list[str]]]:
    """Each failing test once, with the backends it failed on."""
    seen: dict[tuple[str, str], tuple[Failure, list[str]]] = {}
    for job in jobs:
        for failure in job.report.failures if job.report else ():
            entry = seen.setdefault((failure.module, failure.test), (failure, []))
            entry[1].append(_SHORT.get(job.key, job.key))
    return list(seen.values())


def _failure_lines(failures: list[tuple[Failure, list[str]]], run: Run) -> str:
    lines: list[str] = []
    for failure, where in failures[:MAX_NAMED]:
        path = f"{failure.module.replace('.', '/')}.py"
        url = f"https://github.com/{run.repo}/blob/{run.sha}/{path}"
        line = f"• {_link(url, failure.test)} · {', '.join(where)}"
        if failure.message:
            line += f"\n      _{_esc(failure.message)}_"
        lines.append(line)
    if len(failures) > MAX_NAMED:
        lines.append(f"• and {len(failures) - MAX_NAMED} more, in {_link(run.run_url, 'the run')}")
    text = "\n".join(lines)
    return text if len(text) <= _SECTION_LIMIT else f"{text[:_SECTION_LIMIT]}…"


def _steps(jobs: list[Job], run: Run, failures: list[tuple[Failure, list[str]]]) -> str:
    broken = [job for job in jobs if _broken(job)]
    first_log = next((job.log_url for job in broken if job.log_url), run.run_url)
    steps = [f"Open {_link(first_log, "the failing job's log")}."]
    if failures:
        names = " or ".join(dict.fromkeys(f.test for f, _ in failures[:MAX_NAMED]))
        steps.append(
            "Reproduce it against a local Opik (the design doc says how to start one):\n"
            f"```OPIK_URL=http://localhost:8080 make live PYTEST_ARGS=\"-k '{names}'\"```"
        )
    local = next((job for job in broken if job.key == "live-local"), None)
    if local is not None:
        steps.append(f"The {_link(f'{run.run_url}#artifacts', 'backend logs')} are on the run.")
        dispatch = f"https://github.com/{run.repo}/actions/workflows/{WORKFLOW}"
        steps.append(
            "To tell a new Opik release from a change of ours, "
            f"{_link(dispatch, 'run Live by hand')} with `opik_version` set to the last "
            "release that passed."
        )
    return "\n".join(f"{n}. {step}" for n, step in enumerate(steps, start=1))


def build_message(run: Run, jobs: list[Job]) -> dict[str, object]:
    """The Slack Block Kit message for a run with at least one broken job."""
    trigger = _TRIGGERS.get(run.event, run.event)
    commit_url = f"https://github.com/{run.repo}/commit/{run.sha}"
    title = run.commit_title if len(run.commit_title) <= 80 else f"{run.commit_title[:79]}…"
    fields = [
        {"type": "mrkdwn", "text": f"*Trigger*\n{trigger} on `{_esc(run.ref)}`"},
        {"type": "mrkdwn", "text": f"*Commit*\n{_link(commit_url, run.sha[:7])} {_esc(title)}"},
    ]
    fields += [
        {
            "type": "mrkdwn",
            "text": f"*{_LABELS.get(job.key, job.key)} {_esc(job.version)}*\n{_outcome(job)}",
        }
        for job in jobs
    ]
    failures = _failures(jobs)
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔴 opik-mcp live tests failed · {trigger}"},
        },
        {"type": "section", "fields": fields},
    ]
    causes = _causes(jobs)
    if causes:
        text = "*Likely cause*\n" + "\n".join(causes)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    if failures:
        text = f"*Failing tests* ({len(failures)})\n{_failure_lines(failures, run)}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    text = f"*What to do*\n{_steps(jobs, run, failures)}"
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    buttons = [
        {"type": "button", "text": {"type": "plain_text", "text": "View run"}, "url": run.run_url}
    ]
    buttons += [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": f"Log: {_SHORT.get(job.key, job.key)}"},
            "url": job.log_url,
        }
        for job in jobs
        if _broken(job) and job.log_url
    ]
    blocks.append({"type": "actions", "elements": buttons})
    design_doc = f"https://github.com/{run.repo}/blob/main/docs/live-e2e/design-doc.md"
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Advisory: the Live check does not block merges. "
                    f"How the suite works: {_link(design_doc, 'design doc')}.",
                }
            ],
        }
    )
    summary = f"opik-mcp live tests failed · {trigger} on {run.ref}"
    return {"text": summary, "attachments": [{"color": "#d1242f", "blocks": blocks}]}


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
    run = Run(
        repo=repo,
        sha=sha,
        ref=env.get("GITHUB_REF_NAME", ""),
        event=env.get("GITHUB_EVENT_NAME", ""),
        run_url=f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}",
        commit_title=_commit_title(repo, sha),
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

# Live tool e2e

## Purpose

Prove that each tool returns what the user asked for once a real Opik answers.
The unit, conformance and stdio e2e suites run against stubs; they prove the
wire contract and cannot see a filter that selects the wrong traces or a read
too large for the host to accept. When a user-flow evaluation fails, this suite
says whether the tool or the model was wrong.

## What exists

- `scripts/seed_e2e_backend.py` writes a fixed fixture to any Opik over its
  REST API, verifies the backend holds it, and returns a manifest of every
  name, id and count a test may assert. It imports nothing from `opik_mcp`, so
  a bug in the server cannot corrupt the data that tests it.
- `tests/live/` drives `python -m opik_mcp` over stdio with a real MCP client
  and asserts exact values from the manifest. Marker `live`, run by `make live`.
- `.github/workflows/live.yaml` runs it on every pull request, on main and
  nightly, and posts failures on main, nightly and dispatched runs to Slack.

## Architecture

**A fresh fixture per run.** Every run seeds its own fixture: one project named
after the run, plus datasets, experiments, prompts and rules carrying the same
name, all under `mcp-live-run-<run>`. It takes seconds locally and under a
minute on cloud, and the run deletes it at the end with everything its writes
created. No fixture outlives a run, so none can go stale or drift in a shared
workspace. The online rules are created disabled, so cloud never runs the
seeded LLM judges. `tests/test_seed_e2e_backend.py` checks the plan offline:
every record stays under its prefix, and a cloud window keeps every id less
than a day old. The fixture has a content axis
(errored traces, threads, scores, two experiments with known regressions,
Diagnostics issues) and a size axis: a tiny, a typical, a heavy and a wide
trace, a short and a long thread, a small and a wide dataset, a prompt with a
long history, more rules and score names than the project summary lists. Each
large case crosses the limit a read has for it.

**Ids carry time.** `since` and `until` filter on the time inside a record's
UUIDv7 id, not on `start_time`. The seed mints each id from its record's own
instant across two windows of one length: 7 days against a local backend,
10 hours against any other, because Opik cloud refuses an id more than about a
day old. The tests ask for the manifest's exact instants, so the window tests
run on both backends.

**Tests go in through the host's door only.** Nothing under `tests/live/`
imports `opik_mcp`. The suite depends on the tool names, their arguments and
the answer shapes, which the conformance snapshots pin, so moving modules
inside `src/` does not touch it.

**Writes never touch the fixture.** Every record a write test creates,
including the thread it closes and the issue it resolves, lives in a sibling
project of the fixture, `mcp-live-run-<run>-w`, so no write moves a count a
read asserts. Before it starts, a run sweeps what a crashed run left behind
more than two hours ago. Runs do not use the `e2e-cuj-` prefix, which the
shared cloud workspace's own cleanup sweeps on its own schedule and could take
mid-run.

**Sizes.** Every answer's size goes to the job summary. The size tests assert
that each answer stays under what Claude Code accepts, the ceiling defined in
`tests/live/conftest.py`, and that every cut states a count and the call that
gets the rest.

**Two jobs.** `live-local` starts an open source Opik at its latest release
from GHCR images with `opik.sh --backend --port-mapping`, seeds it and runs the
suite. `live-prod` runs the same suite against a shared cloud workspace and
prints the cloud version next to the open source one. It skips with a notice
until `OPIK_E2E_API_KEY` and `OPIK_E2E_WORKSPACE` exist.

## Running it locally

Start an Opik backend, point `OPIK_URL` at it, run `make live`:

```bash
cd <opik checkout> && OPIK_VERSION=<release> ./opik.sh --backend --port-mapping
OPIK_URL=http://localhost:8080 make live
```

On a machine that already runs other Opik stacks, use an isolated compose
project that publishes only the backend on loopback. On Apple Silicon the
minio image's arm64 build has no `wget`, so its healthcheck never passes; the
amd64 build under emulation does:

```yaml
# live.override.yaml, next to docker-compose.yaml
services:
  backend:
    ports: !override
      - "127.0.0.1:28080:8080"
  minio:
    platform: linux/amd64
```

```bash
cd <opik checkout>/deployment/docker-compose
OPIK_VERSION=<release> docker compose -p opik-mcp-live \
  -f docker-compose.yaml -f live.override.yaml --profile backend up -d
OPIK_URL=http://127.0.0.1:28080 make live
```

To seed a fixture by hand, to explore it or for another harness to reuse, and
to delete it again:

```bash
OPIK_URL=http://127.0.0.1:28080 uv run python scripts/seed_e2e_backend.py --prefix my-fixture
OPIK_URL=http://127.0.0.1:28080 uv run python scripts/seed_e2e_backend.py --prefix my-fixture --wipe
```

## Key decisions

- A whole record comes back whole, and its size is stated
  ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)). The heavy
  and wide trace reads are over the host's cap today; their size tests are
  strict xfails, so the day they fit, the marker has to go.
- Every token put into a host's context has to pay for itself
  ([ADR 0001](../decisions/0001-context-budget-first.md)); the size tests are
  where that is measured on real data.

## Test coverage

- Reads and lists per entity: `tests/live/test_traces.py`,
  `tests/live/test_threads.py`, `tests/live/test_project.py`,
  `tests/live/test_evaluation.py`.
- Every write operation an open source backend accepts:
  `tests/live/test_writes.py`.
- Answer sizes and declared cuts: `tests/live/test_sizes.py`, including
  `test_an_answer_fits_what_the_host_accepts` and
  `test_a_wide_trace_accounts_for_every_span`.

## Open questions

- The Diagnostics job operations need Ollie, so their tests skip on an open
  source backend and run only against cloud.
- Whether `live-local` becomes a required check, once it has a record on main.

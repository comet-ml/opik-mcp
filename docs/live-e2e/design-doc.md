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

**The fixture.** One project, `mcp-live-e2e`, plus datasets, experiments,
prompts and rules named with the prefix `mcp-live-`. It has a content axis
(errored traces, threads, scores, two experiments with known regressions,
Diagnostics issues) and a size axis: a tiny, a typical, a heavy and a wide
trace, a short and a long thread, a small and a wide dataset, a prompt with a
long history, more rules and score names than the project summary lists. Each
large case crosses the limit a read has for it.

**Ids carry time.** `since` and `until` filter on the time inside a record's
UUIDv7 id, not on `start_time`. The seed mints each id from its record's own
instant across two windows of one length, 7 days by default. Opik cloud
refuses an id more than about a day old, so there the seed takes
`--window-hours 10`: every id is inside a day when it is written. The tests
ask for the manifest's exact instants, so the windows still hold the data as
it ages, and the window tests run on both backends.

**Deterministic and reusable.** Every id is derived from one anchor instant and
the record's key. The anchor and the fixture version are stored in the project
description. Seeding a backend that already holds the fixture rebuilds the same
manifest from the anchor, verifies it and writes nothing. `--wipe` deletes
the fixture and any run's records older than two hours, never a run that may
still be going. A fixture from another version is refused.

**Tests go in through the host's door only.** Nothing under `tests/live/`
imports `opik_mcp`. The suite depends on the tool names, their arguments and
the answer shapes, which the conformance snapshots pin, so moving modules
inside `src/` does not touch it.

**Writes never touch the fixture.** Every record a write test creates,
including the thread it closes and the issue it resolves, lives in the run's
own project and carries the run's prefix, `e2e-cuj-mcp-live-<run>`. The run
deletes all of it when it ends, and sweeps what a crashed run left behind
before it starts. The `e2e-cuj-` prefix is also swept by the shared cloud
workspace's own cleanup. A second run on the same backend therefore verifies
the fixture again.

**Sizes.** Every answer's size goes to the job summary. The size tests assert
that each answer stays under what Claude Code accepts, the ceiling defined in
`tests/live/conftest.py`, and that every cut states a count and the call that
gets the rest.

**Two jobs.** `live-local` starts an open source Opik at its latest release
from GHCR images with `opik.sh --backend --port-mapping`, seeds it and runs the
suite. `live-prod` runs the whole suite against a shared cloud workspace,
with `OPIK_LIVE_SHARED=1`: it loads the fixture seeded there once by hand and
never seeds or wipes it, and prints the cloud version next to the open source
one. It skips with a notice until
`OPIK_E2E_API_KEY` and `OPIK_E2E_WORKSPACE` exist.

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
uv run python scripts/seed_e2e_backend.py --wipe   # to start clean
```

To seed the production workspace once:

```bash
OPIK_URL=https://www.comet.com/opik/api OPIK_API_KEY=*** OPIK_WORKSPACE=<workspace> \
  uv run python scripts/seed_e2e_backend.py --window-hours 10
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

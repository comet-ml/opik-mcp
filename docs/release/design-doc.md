# release

## Purpose

How a commit on `main` becomes a PyPI version, an image and a Helm chart. It
answers what a merge builds, what a release publishes and in what order, where
the version comes from, and how to finish a release that failed halfway.

## What it does now

### The version number

`version.txt` holds the next unreleased version, `x.y.z`. `bump-version` in
`release.yaml` increments its patch after a release; edit it by hand to choose
another. `make version` generates the git-ignored `src/opik_mcp/_version.py`,
which `pyproject.toml` reads and packs into the wheel. Every build runs it
first. The stamp depends on where the build runs:

| Build | `__version__` | Set by |
|---|---|---|
| Local, PR, branch, manual CI run | `x.y.z.dev0` | `make version` default; `pyver` in `ci.yaml` |
| Image from a push to `main` | `x.y.z` | `pyver` in `ci.yaml` |
| PyPI wheel from a release | `x.y.z` | `VERSION=<version> make version` in the `pypi` job |

A `main` image is stamped plain `x.y.z` because a release promotes that digest
unchanged, so its stamp is what production reports.

### What a pull request or a merge runs

- `build-image` pushes nothing on a pull request and logs in to no registry,
  so fork PRs work. On any other event it pushes `sha-<12-char commit>`, and
  on `main` also `:main`.
- Runs on `main` are not cancelled in progress, since each builds an image a
  release may promote. A queued run can still be dropped; see Traps. PR runs
  cancel older ones.
- `addopts` in `pyproject.toml` keeps the e2e marker out of `make check`.

### What a release run does

`release.yaml` runs only by hand. `validate` pins one commit for every later
job: the tagged commit if the tag `<version>` (no `v`) exists, else `main`'s
HEAD. `create-git-tag` fails on an existing tag unless
`reuse_existing_tag` is true, and on a missing tag when it is.
`promote-image` waits about ten minutes (the `deadline` in its step) for the
`sha-<commit>` image and retags it `:<version>` and `:latest`; if the image
never appears it fails with a message naming the missing tag. It never checks
the tests, so check CI on that commit first. `publish-chart` sets `--version`
and `--app-version` to the release version. `pypi` uses Trusted Publishing; the only secret read is
`GITHUB_TOKEN`. Release permissions, `pypi` approvals and the hosted rollout
live outside this repo.

### Replaying a failed release

If a job after `create-git-tag` fails, re-run with `reuse_existing_tag: true`.
Never delete the tag. `validate` then releases the tagged commit even if `main`
has moved. Every publish step can run twice (PyPI via `skip-existing`), and
`bump-version` runs only after all of them succeed.

### The image and the chart

- The entrypoint is `python -m opik_mcp` under `tini`, so `main()` runs in
  hosted mode as in stdio ([runtime](../runtime/design-doc.md)).
- It runs as numeric user `1000` so Kubernetes `runAsNonRoot` can check it.
- `make docker-run` binds loopback: on Linux `-p 8080:8080` binds everywhere.
- `Chart.yaml` version `0.0.0` is a lint placeholder, replaced at release.

### Legacy TypeScript package

The TypeScript server lives at the tag `legacy-typescript-final` (#203). To
publish, tag a branch off it `npm-v<version>` and dispatch
`legacy-ts-deploy.yml` from that tag. Delete the workflow after 2026-11-15.

### Installing a branch

`make install-branch` registers this worktree with Claude Code at local scope
as `opik-<ticket>`. URL and key come from one source (`resolve_credentials`):
the environment when `OPIK_URL` is set, else `~/.opik.config`; the workspace
is the exception (Traps). An environment key is registered as `${OPIK_API_KEY}`;
a config-file key is stored, with a note. Telemetry is off in that server.

## How it works

```
merge to main
  ci.yaml: version -> build-image => opik-mcp:sha-<commit>, :main
           python-checks, e2e, helm-lint, skills-pack (in parallel)

manual dispatch of release.yaml
  validate -> create-git-tag -> promote-image  => :<version>, :latest
                             -> publish-chart  => charts/opik-mcp:<version>
                             -> pypi           => opik-mcp <version>
  all three succeed          -> github-release -> bump-version (commit to main)
```

To change stamps or image tags, start in `ci.yaml`; release steps, in
`release.yaml`.

- Skills-pack jobs: [skills](../skills/design-doc.md).
- Process lifecycle and bind preflight: [runtime](../runtime/design-doc.md).
- Health endpoints: [hosted-auth](../hosted-auth/design-doc.md).
- Telemetry off in CI: [analytics](../analytics/design-doc.md).

## Decisions

No ADR covers release; the reasons come from workflow comments and PRs.

- Only the release workflow tags, so tags, GitHub releases and PyPI versions
  are one set. When merges were tagged, 0.2.25 to 0.2.27 never shipped (#177).
- A release promotes CI's image instead of rebuilding. Images are tagged by
  commit because `version.txt` is the same between releases (#177).
- `main` images are stamped `x.y.z`: `.dev0` in production fell outside
  analytics cohorts that filter dev builds (#179).
- Replay from the existing tag exists since 0.2.28 reached PyPI but lost the
  race for its image (#178).
- e2e is a job in `ci.yaml`: a separate workflow needs a `paths:` filter,
  which can leave a required check pending (#175).
- A test builds a real wheel to prove skill `evals/` stay out, since the
  editable install cannot show what a wheel holds (#176).
- Not built: `--locked` installs in CI (OPIK-8486); only `Dockerfile` has it.

### Traps

- GitHub keeps at most one pending run per concurrency group and cancels the
  older pending run when a newer one queues, even with
  `cancel-in-progress: false`
  ([docs](https://docs.github.com/en/actions/using-jobs/using-concurrency)).
  A merge between two others can end with no `sha-` image, and a release
  pinned to it fails in `promote-image`.
- A manual CI run on `main` stamps `.dev0` (`pyver` is plain only for `push`),
  queues behind the push run, and re-pushes `sha-<commit>` and `:main`. A
  later release promotes that `.dev0` image. Do not dispatch CI by hand on
  `main`. If it happened, re-run the push-triggered run for that commit (a
  re-run keeps `event_name` as `push`, so the stamp is plain) or push a new
  commit, then release. Re-running an older push run also moves `:main` back.
- `build-image` needs only `version`, so a commit with red tests still gets a
  promotable image.
- `templates/deployment.yaml` uses only `.Values.image.tag` (default `main`)
  and ignores `appVersion`, so a published chart runs `:main` unless the tag
  is overridden.
- With no `OPIK_URL`, an environment `OPIK_WORKSPACE` overrides the config
  file's (`test_config_file_supplies_what_the_environment_does_not`).
- `tests/e2e/test_wheel_contents.py` skips silently when `uv` is not on PATH.

## Proven by

- The wheel holds exactly the served skills: `tests/e2e/test_wheel_contents.py`.
- Install-branch naming, credentials and redaction: `tests/repo/test_install_branch.py`.
- Chart render and image build: `helm-lint` and `build-image` in `ci.yaml`.
- No test runs or parses `.github/workflows/`; a broken release step shows up
  only in a release.

## Log

- 2026-09-24: TypeScript tree removed, Dependabot moved to uv, to stop maintaining the old server (#203).
- 2026-09-24: `make install-branch` runs a worktree as a local MCP server, to try a branch in a real host (#202).
- 2026-09-23: skill `evals/` excluded from the wheel, with a test that builds one, so eval fixtures do not ship (#176).
- 2026-09-01: `main` images stamped `x.y.z` instead of `.dev0`, since the release promotes them unchanged (#179).
- 2026-09-01: release made replayable after a partial failure: `reuse_existing_tag`, PyPI `skip-existing` (#178).
- 2026-09-01: the release workflow creates the tag and bumps `version.txt`; CI stops tagging every merge (#177).
- 2026-06-01: image, chart and PyPI release pipeline added, to host the server (#138).

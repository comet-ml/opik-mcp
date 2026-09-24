# release

## Purpose

This doc covers how a commit on `main` becomes a PyPI version of `opik-mcp`,
a container image and a Helm chart, and how the next version is chosen. Open
it to find out what a merge builds, what a release run publishes and in what
order, where the version number comes from, and how to finish a release that
failed halfway.

## What it does now

### The version number

`version.txt` holds the next version to release, as `x.y.z`. It never names a
version that is already out: the release workflow bumps its patch after a
successful release (`bump-version` in `.github/workflows/release.yaml`). To
pick a different next version, edit `version.txt` by hand.

`src/opik_mcp/_version.py` is generated and git-ignored (`.gitignore`). The
`version` target in `Makefile` writes it. With `VERSION=<v>` set it writes
that value; otherwise it writes `<version.txt>.dev0`. `pyproject.toml` reads
the package version from this file (`[tool.hatch.version]`) and lists it as a
build artifact (`[tool.hatch.build] artifacts`), so a wheel or sdist carries
it even though git does not. `make install`, `make check` and
`make docker-build` all run `make version` first. Every CI job that builds or
tests runs it before `uv build` or the image build
(`.github/workflows/ci.yaml`).

What a build is stamped with depends on where it runs:

| Build | `__version__` | Set by |
|---|---|---|
| Local checkout, PR build, branch build | `x.y.z.dev0` | `make version` default; `pyver` output of the `version` job in `ci.yaml` off `main` |
| Image built from a push to `main` | `x.y.z` | `pyver` output of the `version` job in `ci.yaml` on `main` |
| PyPI wheel from a release | `x.y.z` | `VERSION=<version> make version` in the `pypi` job of `release.yaml` |

A `main` image carries the plain `x.y.z` because a release promotes that image
digest without rebuilding it, so its stamp is the version production reports
(comment in the `version` job of `ci.yaml`).

### What a pull request or a merge runs

`.github/workflows/ci.yaml` runs on every pull request to `main`, every push to
`main`, and on manual dispatch. The whole workflow sets
`OPIK_MCP_ANALYTICS_ENABLED=false`, `OPIK_MCP_SENTRY_ENABLED=false` and
`DO_NOT_TRACK=1`, so no job sends telemetry (the reason is in
[analytics](../analytics/design-doc.md)). Its jobs:

- `version` reads `version.txt`, fails if it is not `x.y.z`, and warns if a
  git tag for that version already exists, which means `version.txt` was not
  bumped after the last release. It outputs the version, the stamp (`pyver`)
  and `sha-<12-char commit>` as the image tag.
- `python-checks` runs `make check`.
- `e2e` runs `make e2e`: the server as a real subprocess over stdio, plus the
  wheel build in `tests/e2e/test_wheel_contents.py`. The e2e marker is
  deselected from `make check` by `addopts` in `pyproject.toml`.
- `skills-pack` and `publish-skills-pack` build, verify and publish the skills
  pack. Only their place in CI belongs here; the rest is in
  [skills](../skills/design-doc.md).
- `helm-lint` runs `helm lint` and `helm template` on `helm/opik-mcp`.
- `build-image` builds `Dockerfile` for `linux/amd64` and `linux/arm64`. On a
  pull request it builds and pushes nothing, with no registry login, so fork
  PRs work. On a push it pushes `ghcr.io/comet-ml/opik-mcp:sha-<commit>`, and on
  `main` also `:main`.

CI never creates a git tag. On a pull request a newer run cancels the older
one; pushes to `main` are never cancelled, because each one produces an image
a release may promote (`concurrency` in `ci.yaml`).

`.github/workflows/release-drafter.yml` runs on every push to `main` and keeps
a draft GitHub release listing merged PRs, using the template in
`.github/release-drafter.yml`.

### What a release run does

A release is `.github/workflows/release.yaml`, started by hand
(`workflow_dispatch`). Nothing in the repo starts it on a merge. It takes one
input, `reuse_existing_tag`, false by default. The jobs run in this order:

1. `validate` checks out `main`, reads `version.txt` and checks the `x.y.z`
   form. It pins one commit for every later job. If the tag already exists on
   the remote it releases the tagged commit; otherwise it releases `main`'s
   HEAD.
2. `create-git-tag` creates and pushes the tag `<version>` at that commit (no
   `v` prefix). If the tag already exists it fails with a message saying to
   bump `version.txt` or replay. With `reuse_existing_tag: true` it accepts an
   existing tag and fails if there is none.
3. Three jobs run after the tag, in parallel:
   - `promote-image` waits up to ten minutes for the `sha-<commit>` image
     that CI built, then retags it as `:<version>` and `:latest` with
     `docker buildx imagetools create`. The image is not rebuilt, so the
     released digest is the one CI built for that commit. The job waits for
     the image only; it does not check that the tests passed on that commit.
     Whoever starts the release checks CI first.
   - `publish-chart` runs `helm package helm/opik-mcp` with `--version` and
     `--app-version` set to the release version and pushes it to
     `oci://ghcr.io/comet-ml/charts`. The `0.0.0` in
     `helm/opik-mcp/Chart.yaml` is a placeholder used only by lint.
   - `pypi` runs `VERSION=<version> make version`, `uv build`, and publishes
     with `pypa/gh-action-pypi-publish` under the `pypi` environment, using
     PyPI Trusted Publishing (`id-token: write`); no PyPI token is stored.
     `skip-existing: true` lets a replay pass a version already uploaded.
4. `github-release` creates the GitHub release for the tag with
   `--generate-notes`, or leaves it alone if it exists, then deletes the
   leftover release-drafter drafts.
5. `bump-version` checks out `main` and, if `version.txt` still equals the
   released version, commits `version.txt` with the patch incremented and
   pushes to `main` as `github-actions`. If someone changed `version.txt`
   during the release it leaves the file alone and warns.

The only secret the Python workflows read is `GITHUB_TOKEN`, used for GHCR,
the Helm registry and `gh`.

Who may start a release, and any approval rule on the `pypi` environment, is
set in the GitHub repository settings, not in a file here. Nothing in this repo
moves the hosted server to a new image tag; that rollout happens outside it.

### Replaying a failed release

If a job after `create-git-tag` fails, re-run the workflow with
`reuse_existing_tag: true`. Do not delete the tag. `validate` then releases the
commit the tag points at, even if `main` has moved, so the tag, the wheel and
the image stay on one commit. Every publish step is safe to run twice: the
image retag, PyPI (`skip-existing`), and the GitHub release (checked with
`gh release view` first). `bump-version` runs only when every earlier job
succeeded, so a failed version is never skipped over. The comments in
`release.yaml` describe the 0.2.28 release that failed this way.

### The image

`Dockerfile` has two stages. The build stage (`ghcr.io/astral-sh/uv`,
Python 3.13) runs `uv sync --locked --no-dev`, first without the project so
dependencies cache in their own layer, then with it. `COPY src` includes the
generated `_version.py`, so `version.txt` is not copied. The runtime stage
(`python:3.13-slim-bookworm`) copies only the venv, installs `tini`, and runs
as numeric user `1000` so Kubernetes `runAsNonRoot` can check it. It sets
`OPIK_MCP_TRANSPORT=http`, `OPIK_MCP_HOST=0.0.0.0`, `OPIK_MCP_PORT=8080` and
exposes 8080.

The entrypoint is `tini -- python -m opik_mcp`, which runs `main()` instead of
starting uvicorn directly. `main()` emits the lifecycle events and runs the
bind preflight and OAuth config guard before serving (comment in
`Dockerfile`; the process itself is in [runtime](../runtime/design-doc.md), the
events in [analytics](../analytics/design-doc.md)). The comment also states it
runs a single worker by design.

`.dockerignore` keeps `tests`, `docs`, `legacy`, `.github`, `.env*`, the
`Makefile` and build output out of the build context.

`make docker-build` builds `opik-mcp:dev` locally. `make docker-run` runs it
published on `127.0.0.1:8080` only, because on Linux `-p 8080:8080` listens on
every interface (comment in `Makefile`).

### The Helm chart

`helm/opik-mcp/` deploys the image with a Deployment, Service, ServiceAccount
and an optional Ingress. `values.yaml` defaults to image tag `main`, one
replica, liveness on `/health`, readiness and startup on `/health/ready`, a
60-second termination grace period for open streams, `runAsNonRoot`, a
read-only root filesystem and all capabilities dropped. Ingress is off by
default. What the health endpoints check is in
[hosted-auth](../hosted-auth/design-doc.md).

### The legacy TypeScript package

The old TypeScript server, published to npm under the same name, `opik-mcp`,
left `main` in #203 and lives at the tag `legacy-typescript-final`. Only
`.github/workflows/legacy-ts-deploy.yml` remains, run by manual dispatch: to
publish, branch off that tag, tag the branch `npm-v<version>` and dispatch from
that tag ref. It publishes to npm with provenance (OIDC, no token). The file's
comment says to delete it after 2026-11-15. The `npm-v*` tags are separate from
the Python `x.y.z` tags.

### Dependency updates and labels

`.github/dependabot.yml` opens weekly updates for Python through the `uv`
ecosystem, which updates `uv.lock` in the same PR, with a cooldown before a new
release is taken, and for GitHub Actions. The CI jobs install with
`uv sync --extra dev`, without `--locked`; only the `Dockerfile` uses
`--locked`.

### Installing a branch for local testing

`make install-branch` runs `scripts/dev/install_branch.py install`. It builds a
snapshot venv of the current worktree (running `make version` first), and
registers it with Claude Code at local scope as `opik-<ticket>`, where the
ticket comes from the branch name (`server_name`). Local scope means it loads
only in sessions inside this repo. `make uninstall-branch` removes it. The
same script has `dogfood-prepare`, `dogfood-run` and `dogfood-clean`, which
build this branch and `origin/main` side by side for one headless run.

Credentials come from one source (`resolve_credentials`): the environment
when `OPIK_URL` is set, otherwise `~/.opik.config`. The script takes no key
argument. A key from the environment is registered as the reference
`${OPIK_API_KEY}`. A key read from `~/.opik.config` has to be stored, and the
script prints a note saying so. The key is redacted from everything it prints
(`Runner.redacted`), and the registered server always has analytics and Sentry
turned off (`server_env`).

## How it works

A merge to `main` and a release are two separate steps:

```
merge to main
  ci.yaml:        version -> build-image  => ghcr.io/comet-ml/opik-mcp:sha-<commit>, :main
                  python-checks, e2e, helm-lint, skills-pack (-> publish-skills-pack)
  release-drafter.yml: update the draft release

manual dispatch of release.yaml
  validate -> create-git-tag -> promote-image   => :<version>, :latest
                             -> publish-chart   => oci://ghcr.io/comet-ml/charts/opik-mcp:<version>
                             -> pypi            => opik-mcp <version> on PyPI
           -> github-release (needs all three) -> bump-version (commit to main)
```

This feature owns `.github/workflows/`, `.github/release-drafter.yml`,
`.github/dependabot.yml`, `Dockerfile`, `.dockerignore`,
`helm/opik-mcp/`, the `version`, `install`, `docker-*` and `install-branch`
targets in `Makefile`, `version.txt`, `src/opik_mcp/_version.py`, the
`[tool.hatch.*]` blocks in `pyproject.toml`, and `scripts/dev/install_branch.py`.

Its boundaries:

- The skills-pack jobs sit in `ci.yaml`, but what they build and verify is
  [skills](../skills/design-doc.md).
- What the image runs once started (transport, bind, config) is
  [runtime](../runtime/design-doc.md); the HTTP app, auth and health endpoints
  are [hosted-auth](../hosted-auth/design-doc.md).
- Why telemetry is off in CI and tests is [analytics](../analytics/design-doc.md).

## Decisions

- No ADR covers release. The reasons below are from workflow comments and PRs.
- The release workflow creates the tag, and CI never does, so tags, GitHub
  releases and PyPI versions are the same set. When merges were tagged,
  0.2.25 to 0.2.27 were tagged and never released (#177).
- `version.txt` names the next unreleased version and is bumped after a
  successful release, so a release has no version to type and cannot collide
  with a published one (#177).
- A release promotes the image CI built for the commit instead of rebuilding,
  so the released digest is the tested one. Images are tagged by commit
  because `version.txt` is the same for every build between releases (#177).
- Images built on `main` are stamped `x.y.z` instead of `x.y.z.dev0`, because
  the promoted image reports that version forever; `.dev0` in production put
  those builds outside every analytics cohort that filters dev builds (#179).
- A release can be replayed from its existing tag, and each publish step is
  idempotent, after 0.2.28 uploaded to PyPI but lost the race for its image
  (#178).
- PyPI uses Trusted Publishing, so the repo stores no PyPI token
  (`release.yaml`).
- The image runs `python -m opik_mcp` so that `main()` owns the lifecycle
  events, the bind preflight and the OAuth guard in hosted mode as it does in
  stdio (`Dockerfile`).
- The e2e suite is a job in `ci.yaml` instead of its own workflow: it is
  hermetic and quick, and a separate workflow would need a `paths:` filter
  that can leave a required check pending (#175).
- Skill `evals/` are excluded from the wheel, and a test builds a real wheel to
  prove it, since the editable install used by other tests cannot show what a
  wheel contains (#176).
- The TypeScript source left `main`; its publish workflow stays, manual only,
  so a last npm release can still be cut from the tag (#203).
- Not built: CI installing with `--locked`, filed as OPIK-8486.
- Not built: tests for the workflows themselves. Nothing in `tests/` runs or
  parses `.github/workflows/`; a broken release step shows up only when a
  release runs.

## Proven by

- `tests/e2e/test_wheel_contents.py`: builds a wheel with `uv build` and
  checks it against what the server serves.
  - `test_every_served_skill_document_is_in_the_wheel`: every skill file the
    server serves is packaged.
  - `test_the_wheel_carries_nothing_the_server_will_not_serve`: the wheel has
    no skill files the server would not serve.
  - `test_excluded_directories_never_reach_the_wheel`: `evals/` and the other
    excluded directories are left out.
  - `test_the_wheel_is_importable_and_serves_skills_from_it`: the installed
    wheel imports and serves skills.
- `tests/test_install_branch.py`: the behaviour of
  `scripts/dev/install_branch.py`.
  - `test_the_server_is_named_after_the_branch`: the server name comes from
    the ticket in the branch.
  - `test_the_key_comes_from_the_same_source_as_the_url`: the environment and
    `~/.opik.config` are never mixed.
  - `test_the_key_is_never_printed`, `test_a_failed_registration_does_not_print_the_key`
    and `test_redaction_replaces_the_whole_key_only`: the key stays out of
    output.
  - `test_the_script_takes_no_key_argument`: there is no CLI flag for a key.
  - `test_telemetry_is_always_off`: the registered server has analytics and
    Sentry disabled.
  - `test_install_is_scoped_to_this_repo`: registration uses local scope.
  - `test_an_env_key_is_stored_as_a_reference`: an environment key is stored
    as `${OPIK_API_KEY}`.
  - `test_a_name_that_could_escape_its_folder_is_refused`: a name cannot
    point a delete outside its folder.
- The CI jobs `helm-lint` (chart renders) and `build-image` (image builds for
  both platforms) run on every PR in `.github/workflows/ci.yaml`.
- No test covers `release.yaml`, `release-drafter.yml`, `legacy-ts-deploy.yml` or `Dockerfile` beyond the CI build.

## Log

- 2026-09-24: TypeScript tree removed, Dependabot moved to uv, labeler dropped, to stop maintaining the old server (#203).
- 2026-09-24: `make install-branch` runs a worktree as a local MCP server, to try a branch in a real host (#202).
- 2026-09-23: skill `evals/` excluded from the wheel, with a test that builds one, so eval fixtures do not ship (#176).
- 2026-09-01: `main` images stamped `x.y.z` instead of `.dev0`, since the release promotes them unchanged (#179).
- 2026-09-01: release made replayable after a partial failure: `reuse_existing_tag`, PyPI `skip-existing` (#178).
- 2026-09-01: the release workflow creates the tag and bumps `version.txt`; CI stops tagging every merge (#177).
- 2026-09-01: e2e job added to `ci.yaml` and telemetry turned off for the whole workflow, so CI sends no events (#175).
- 2026-06-04: image runs as numeric user 1000, for Kubernetes `runAsNonRoot` (#143).
- 2026-06-01: image, chart and PyPI release pipeline and the Helm chart added, to host the server (#138).
- 2026-05-25: `Dockerfile` and health probes added, for hosting (#122).

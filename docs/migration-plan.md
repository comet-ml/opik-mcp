# Migration plan: TypeScript → Python

Status: **planned, not executed** — revision 2 after full review.
Author: drafted in this repo before the migration begins.
Target repo: `comet-ml/opik-mcp` (this repo, unchanged).

## Review-pass additions (rev 2)

Items the v1 draft missed, now added below:

- **PyPI publishing track** as a full Phase 7 — including `pyproject.toml` enrichment, TestPyPI dry-run, Trusted Publishing config, `uvx` smoke test from a clean machine.
- **PyPI name confirmed available** (`opik-mcp` returns HTTP 404 on `pypi.org/pypi/opik-mcp/json` as of this writing — must be claimed on first publish).
- **`pyproject.toml` gaps** — current file lacks `urls`, `keywords`, `classifiers`, production-grade description; the `requires-python = ">=3.13"` floor is aggressive and should be reviewed.
- **User-facing breakages** — CLI flags → env vars, toolset model changes, env-var renames; needs a dedicated user migration guide (Phase 8).
- **`.github/labeler.yml` rewrite** — currently references TS paths and will misfire on every PR after the move.
- **`CODEOWNERS` references `.github/release-drafter.yml`** which doesn't exist in the repo today — must add or drop the line.
- **`server.json` for the Python distribution** — the existing one advertises only the npm package. Decision required: extend `packages[]` to include PyPI, or publish a second registry entry.
- **README badges** — Node.js/TypeScript badges in the legacy README need Python/PyPI equivalents at root.
- **Branch protection rules** — required status check `CI` (current workflow name) will be stale after renames; settings update needed post-merge.
- **`.claude/` from Python repo** — only `settings.local.json`, not worth copying. Open question resolved.

---

## Goal

Replace the TypeScript MCP server at the root of this repo with the Python version from `/Users/yaroslavboiko/AwKoY/opik-mcp`, while keeping the TypeScript code, build, and npm release pipeline alive under `legacy/typescript/` for users who depend on it.

## Decisions (locked)

1. **Python history is not preserved.** We copy the Python source tree into this repo; the two bootstrap commits in `comet-ml/opik-mcp` (`d44c96d`, `2c70260` — both docs) are not imported.
2. **Both distributions keep the name `opik-mcp`.** The npm package and PyPI package coexist by name because users invoke them via `npx -y opik-mcp` and `uvx opik-mcp` respectively — different binaries, different registries, no collision at install time.
3. **TypeScript keeps shipping.** npm releases continue from `legacy/typescript/` with the existing `v2.x.x` tag scheme. Python releases get a separate tag prefix (see "Tagging" below).
4. **`main` becomes Python.** Root README, root Makefile, and root CI describe Python. Anything TypeScript lives under `legacy/typescript/`.

## Target repo layout

```
opik-mcp/
├── .github/
│   ├── CODEOWNERS
│   ├── dependabot.yml             ← updated: two ecosystems
│   ├── ISSUE_TEMPLATE/
│   ├── labeler.yml
│   ├── pull_request_template.md
│   └── workflows/
│       ├── python-ci.yml          ← NEW
│       ├── python-release.yml     ← NEW (PyPI publish on py-v* tags)
│       ├── legacy-ts-ci.yml       ← renamed from ci.yml, path-filtered
│       └── legacy-ts-deploy.yml   ← renamed from deploy.yml, working-directory set
├── .gitignore                     ← merged (Python rules + legacy/typescript/ build paths)
├── .claude/                       ← copied from Python repo (optional)
├── CITATION.cff                   ← unchanged
├── LICENSE                        ← unchanged
├── Makefile                       ← Python targets + `legacy-*` proxy targets
├── README.md                      ← Python-first, with prominent pointer to legacy/
├── pyproject.toml                 ← from Python repo
├── uv.lock                        ← from Python repo
├── docs/                          ← from Python repo (architecture.md, design.md, decisions/, etc.)
│   └── migration-plan.md          ← this file (historical record)
├── src/
│   └── opik_mcp/                  ← from Python repo
├── tests/                         ← from Python repo
└── legacy/
    └── typescript/
        ├── DEPRECATED.md          ← NEW: EOL policy, migration pointer
        ├── README.md              ← original TS README + deprecation banner
        ├── package.json
        ├── package-lock.json
        ├── tsconfig.json
        ├── tsconfig.test.json
        ├── .eslintrc.json
        ├── .prettierrc
        ├── .env.example
        ├── .cursor/
        ├── Dockerfile
        ├── smithery.yaml
        ├── server.json
        ├── Makefile               ← original TS Makefile (kept verbatim)
        ├── CONTRIBUTING.md
        ├── test-client.js
        ├── src/
        ├── tests/
        ├── docs/                  ← TS-specific: api-reference.md, configuration.md, etc.
        └── scripts/
```

## File-by-file move map

Run from the repo root after creating the migration branch. All `git mv` calls preserve history (use `git log --follow` afterward).

### Move to `legacy/typescript/`

| Source (current location)        | Destination                              |
| -------------------------------- | ---------------------------------------- |
| `src/`                           | `legacy/typescript/src/`                 |
| `tests/`                         | `legacy/typescript/tests/`               |
| `docs/`                          | `legacy/typescript/docs/`                |
| `scripts/`                       | `legacy/typescript/scripts/`             |
| `client/`                        | `legacy/typescript/client/`              |
| `.cursor/`                       | `legacy/typescript/.cursor/`             |
| `package.json`                   | `legacy/typescript/package.json`         |
| `package-lock.json`              | `legacy/typescript/package-lock.json`    |
| `tsconfig.json`                  | `legacy/typescript/tsconfig.json`        |
| `tsconfig.test.json`             | `legacy/typescript/tsconfig.test.json`   |
| `.eslintrc.json`                 | `legacy/typescript/.eslintrc.json`       |
| `.prettierrc`                    | `legacy/typescript/.prettierrc`          |
| `.env.example`                   | `legacy/typescript/.env.example`         |
| `Dockerfile`                     | `legacy/typescript/Dockerfile`           |
| `smithery.yaml`                  | `legacy/typescript/smithery.yaml`        |
| `server.json`                    | `legacy/typescript/server.json`          |
| `test-client.js`                 | `legacy/typescript/test-client.js`       |
| `Makefile`                       | `legacy/typescript/Makefile`             |
| `CONTRIBUTING.md`                | `legacy/typescript/CONTRIBUTING.md`      |
| `README.md`                      | `legacy/typescript/README.md`            |
| `.gitignore`                     | (replaced — see "Root files to rewrite") |

### Re-create or copy at root (from Python repo `/Users/yaroslavboiko/AwKoY/opik-mcp/`)

| Source (Python repo)             | Destination (this repo)                  |
| -------------------------------- | ---------------------------------------- |
| `src/opik_mcp/`                  | `src/opik_mcp/`                          |
| `tests/`                         | `tests/`                                 |
| `docs/`                          | `docs/` (merge — preserve `docs/migration-plan.md`) |
| `pyproject.toml`                 | `pyproject.toml`                         |
| `uv.lock`                        | `uv.lock`                                |
| `Makefile`                       | `Makefile` (then extend with `legacy-*` targets) |
| `README.md`                      | `README.md`                              |
| `.gitignore`                     | merged into root `.gitignore`            |
| `.claude/`                       | `.claude/` (optional — useful if devs use Claude Code here) |

### Stays at root, unchanged

- `.git/` (obviously)
- `LICENSE`
- `CITATION.cff`
- `.github/CODEOWNERS`, `.github/ISSUE_TEMPLATE/`, `.github/labeler.yml`, `.github/pull_request_template.md`

### Stays at root, edited

- `.github/workflows/*` — see "CI changes" below
- `.github/dependabot.yml` — see "CI changes" below

---

## Execution steps

### Pre-flight (10 min)

1. **Confirm both working trees are clean.**
   ```bash
   cd /Users/yaroslavboiko/AwKoY/aa/opik-mcp && git status
   cd /Users/yaroslavboiko/AwKoY/opik-mcp && git status
   ```
2. **Tag the pre-migration TS state.** This is the rollback anchor.
   ```bash
   cd /Users/yaroslavboiko/AwKoY/aa/opik-mcp
   git tag -a ts-v2.0.1-final -m "Last commit before Python migration. TypeScript root layout."
   git push origin ts-v2.0.1-final
   ```
   Note: keeping the existing `v2.0.1` tag too — the new tag is purely additive.
3. **Create the migration branch.**
   ```bash
   git checkout -b chore/migrate-to-python
   ```

### Phase 1 — move TS into `legacy/typescript/` (15 min)

1. Create the directory:
   ```bash
   mkdir -p legacy/typescript
   ```
2. `git mv` every entry listed in the "Move to `legacy/typescript/`" table above. Example:
   ```bash
   git mv src legacy/typescript/src
   git mv tests legacy/typescript/tests
   git mv docs legacy/typescript/docs
   git mv package.json legacy/typescript/
   # ... etc, full list in the table
   ```
3. Commit:
   ```bash
   git commit -m "chore: move TypeScript server into legacy/typescript/"
   ```

### Phase 2 — copy Python code to root (10 min)

1. Copy from the Python repo (plain `cp -R`, no git history merge):
   ```bash
   SRC=/Users/yaroslavboiko/AwKoY/opik-mcp
   cp    $SRC/pyproject.toml ./
   cp    $SRC/uv.lock ./
   cp    $SRC/README.md ./
   cp    $SRC/Makefile ./
   cp -R $SRC/src/opik_mcp ./src/
   cp -R $SRC/tests ./
   # Merge docs/ — Python docs go into existing docs/ alongside migration-plan.md
   cp -R $SRC/docs/. ./docs/
   # Optional: copy .claude/ if devs in this repo use Claude Code skills/agents
   cp -R $SRC/.claude ./
   ```
2. Write the new root `.gitignore` (merge — see "Root files to rewrite").
3. Commit:
   ```bash
   git add -A
   git commit -m "feat: bring Python MCP server in as primary implementation"
   ```

### Phase 3 — root README, Makefile, deprecation notice (15 min)

1. **Rewrite root `README.md`** — Python-first. Top of the file must contain a callout pointing legacy users at `legacy/typescript/`:
   ```markdown
   > **Looking for the TypeScript v2 server?** It still ships on npm as `opik-mcp@^2`
   > (`npx -y opik-mcp`) and the source lives in [`legacy/typescript/`](./legacy/typescript/).
   > See [`legacy/typescript/DEPRECATED.md`](./legacy/typescript/DEPRECATED.md) for support policy.
   ```
2. **Rewrite root `Makefile`** — start from the Python Makefile (which has `install/run/test/lint/format/typecheck/check/inspect/run-dev`), then append legacy proxy targets:
   ```makefile
   # --- Legacy TypeScript server (deprecated) ---
   .PHONY: legacy-install legacy-build legacy-test legacy-lint legacy-start

   legacy-install:
   	$(MAKE) -C legacy/typescript install

   legacy-build:
   	$(MAKE) -C legacy/typescript build

   legacy-test:
   	$(MAKE) -C legacy/typescript test

   legacy-lint:
   	$(MAKE) -C legacy/typescript lint

   legacy-start:
   	$(MAKE) -C legacy/typescript start
   ```
3. **Create `legacy/typescript/DEPRECATED.md`**:
   ```markdown
   # TypeScript Opik MCP server — deprecated

   This implementation is in maintenance-only mode. The Python implementation at the repo root is the supported version going forward.

   - **Last feature release:** `v2.0.1` (npm `opik-mcp@2.0.1`)
   - **Security-patch policy:** critical CVEs only, until <DATE — fill in>
   - **End of life:** <DATE — fill in>
   - **Migration:** install via `uvx opik-mcp` instead of `npx -y opik-mcp`. Tools, transports, and config env vars are renamed/restructured — see the root [`README.md`](../../README.md) and `docs/` for the new surface.

   The TypeScript code remains buildable and testable in place:

   ```bash
   cd legacy/typescript
   npm install
   npm run build
   npm test
   ```

   Or from the repo root via `make legacy-install`, `make legacy-build`, etc.
   ```
4. **Prepend a deprecation banner to `legacy/typescript/README.md`** (above the existing `<h1>`):
   ```markdown
   > ⚠️ **Deprecated.** This is the v2 TypeScript implementation, kept for backward compatibility.
   > New users should install the Python server: `uvx opik-mcp`. See the repo root README
   > and [`DEPRECATED.md`](./DEPRECATED.md) for the support policy.
   ```
5. Commit:
   ```bash
   git add -A
   git commit -m "docs: rewrite root README/Makefile for Python, add legacy deprecation notice"
   ```

### Phase 4 — CI rewrites (20 min)

See "CI changes" section below for the exact YAML edits. Commit as:
```bash
git commit -m "ci: split workflows for Python (root) and legacy TypeScript (legacy/typescript/)"
```

### Phase 5 — verify locally (15 min)

Run the "Verification checklist" below. Fix anything that breaks.

### Phase 6 — open PR

```bash
git push -u origin chore/migrate-to-python
gh pr create --title "chore: migrate to Python implementation, move TS to legacy/typescript/" \
  --body "See docs/migration-plan.md for context."
```

The PR will be large but mostly mechanical moves. Reviewers should focus on:
- Root README (does the legacy pointer read clearly?)
- `legacy/typescript/DEPRECATED.md` (policy correct? dates filled in?)
- The four workflow files
- Root `.gitignore` (no rules dropped)
- The labeler.yml rewrite (see "Repo metadata files to update")

### Phase 7 — publish Python `opik-mcp` to PyPI for `uvx` distribution

**This phase can run independently from the migration PR** — `uv build` operates on a directory, so you could publish from the Python source repo (`/Users/yaroslavboiko/AwKoY/opik-mcp/`) *before* the migration merges if you want to validate the release pipeline early. The instructions below assume execution post-merge from this repo's root, which is the canonical path.

#### 7.1 Pre-publish `pyproject.toml` enrichment (one-time)

The current `pyproject.toml` was sized for a PoC. PyPI needs more. Apply this diff to the root `pyproject.toml` before the first build:

```toml
[project]
name = "opik-mcp"
version = "0.1.0"                                # bump from 0.0.1 — first real release
description = "Model Context Protocol server for Opik: prompts, traces, datasets, metrics, and Ollie."
readme = "README.md"
requires-python = ">=3.11"                       # see open question Q6 — was >=3.13
license = "Apache-2.0"                           # SPDX expression (PEP 639)
license-files = ["LICENSE"]
authors = [
    { name = "Comet ML, Inc." },
    { name = "Yaroslav Boiko" },
    { name = "Vincent Koc" },
]
maintainers = [{ name = "Comet ML, Inc.", email = "support@comet.com" }]
keywords = ["mcp", "model-context-protocol", "opik", "llm", "observability", "prompts", "traces"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]

[project.urls]
Homepage = "https://github.com/comet-ml/opik-mcp"
Documentation = "https://github.com/comet-ml/opik-mcp#readme"
Repository = "https://github.com/comet-ml/opik-mcp"
Issues = "https://github.com/comet-ml/opik-mcp/issues"
Changelog = "https://github.com/comet-ml/opik-mcp/blob/main/CHANGELOG.md"

# dependencies, scripts, etc. remain unchanged
```

Notes:
- `name = "opik-mcp"` will be **claimed on first publish** — confirmed available on PyPI as of this writing.
- `license = "Apache-2.0"` as a string is the PEP 639 SPDX form; if it triggers a build error on older hatchling, fall back to `license = { text = "Apache-2.0" }`.
- Bumping `requires-python` down from `>=3.13` to `>=3.11` widens the user base 3× and matches what most ML workstations have today. **Verify the codebase actually runs on 3.11** — quick CI matrix check before the version pin lands. If 3.13-only features are used (e.g., `type` statement syntax, PEP 695 generics, `defer`), keep `>=3.13`.
- `[project.scripts]` already has `opik-mcp = "opik_mcp.__main__:main"` (verified). This is what `uvx opik-mcp` resolves to.

#### 7.2 First-time PyPI Trusted Publishing setup (manual, one-time)

Avoids long-lived API tokens. Done once per project.

1. Log into https://pypi.org with the account that should own the project.
2. Go to https://pypi.org/manage/account/publishing/ → "Add a new pending publisher".
3. Fill in:
   - **PyPI Project Name**: `opik-mcp`
   - **Owner**: `comet-ml`
   - **Repository name**: `opik-mcp`
   - **Workflow name**: `python-release.yml`
   - **Environment name**: `pypi` (matches `environment: pypi` block in the workflow — see 7.3)
4. Repeat on https://test.pypi.org/manage/account/publishing/ with the **same project name** for TestPyPI dry-runs (use a separate environment name like `testpypi`).
5. In the GitHub repo: Settings → Environments → New environment → `pypi` (and `testpypi`). Optionally add required reviewers for `pypi` to gate production publishes.

#### 7.3 Update `python-release.yml` for TestPyPI + PyPI

Replace the placeholder workflow from Phase 4 with this two-stage version. Same file, expanded:

```yaml
name: Python Release

on:
  push:
    tags:
      - "py-v*"
  workflow_dispatch:
    inputs:
      target:
        description: "Publish target"
        required: true
        default: "testpypi"
        type: choice
        options: [testpypi, pypi]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install 3.13
      - run: uv sync --extra dev
      - run: make check
      - run: uv build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish-testpypi:
    needs: build
    if: github.event_name == 'workflow_dispatch' && inputs.target == 'testpypi'
    runs-on: ubuntu-latest
    environment: testpypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: https://test.pypi.org/legacy/

  publish-pypi:
    needs: build
    # Tag pushes always publish to real PyPI; manual dispatch must pick "pypi"
    if: |
      startsWith(github.ref, 'refs/tags/py-v') ||
      (github.event_name == 'workflow_dispatch' && inputs.target == 'pypi')
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
```

#### 7.4 First-release procedure (executed once)

```bash
# 1. From main, on the post-migration HEAD, locally verify the build:
uv sync --extra dev
make check                                    # lint + typecheck + test must pass
uv build                                      # produces dist/*.whl and dist/*.tar.gz
uv run twine check dist/*                     # validate metadata renders for PyPI
unzip -l dist/opik_mcp-0.1.0-*.whl | head    # eyeball the wheel contents

# 2. Dry-run on TestPyPI via the GH UI (Actions → Python Release → Run workflow → target=testpypi)
#    Wait for green. Then verify installability from a clean machine/container:
docker run --rm -it python:3.11-slim bash -lc '
  pip install --index-url https://test.pypi.org/simple/ \
              --extra-index-url https://pypi.org/simple/ opik-mcp==0.1.0 &&
  opik-mcp --help || true                     # entry point should exist
'

# 3. uvx smoke test against TestPyPI (uvx supports --index)
docker run --rm -it python:3.11-slim bash -lc '
  pip install uv &&
  uv tool run --index-url https://test.pypi.org/simple/ \
              --extra-index-url https://pypi.org/simple/ \
              opik-mcp@0.1.0
'

# 4. Once TestPyPI is verified, tag and push for the real publish:
git tag -a py-v0.1.0 -m "First PyPI release: Python MCP server for Opik."
git push origin py-v0.1.0
# python-release.yml picks up the tag, builds, and publishes to PyPI via Trusted Publishing.

# 5. Production smoke test from a clean environment:
docker run --rm -it python:3.11-slim bash -lc '
  pip install uv && uvx opik-mcp@0.1.0       # must boot in stdio mode by default
'
```

Once this is verified, the `uvx opik-mcp` command in the new README is real, and the per-host MCP config snippets (Cursor, VS Code, Windsurf — Phase 8 / post-migration task #3) can land.

#### 7.5 Subsequent releases

```bash
# In pyproject.toml: bump version to 0.2.0 (or whatever)
# Commit, then:
git tag -a py-v0.2.0 -m "..."
git push origin py-v0.2.0
# CI takes it from there.
```

No manual `uv build` / `twine upload` in the steady state — only via tag.

### Phase 8 — write user migration guide

Separate from this maintainer-facing plan. Write a `docs/migrate-from-typescript.md` that maps:

| Old (TS, npm `opik-mcp@2.x`)               | New (Python, PyPI `opik-mcp`)            |
| ------------------------------------------ | ---------------------------------------- |
| `npx -y opik-mcp --apiKey ...`             | `OPIK_API_KEY=... uvx opik-mcp`          |
| `--apiUrl http://localhost:5173/api`       | `OPIK_URL_OVERRIDE=http://...` (env)     |
| `--toolsets core,expert-prompts`           | `OPIK_TOOLSETS=...` (env, if supported)  |
| `--transport stdio` / `streamable-http`    | `OPIK_MCP_TRANSPORT=stdio` / `http`      |
| `STREAMABLE_HTTP_PORT=3001`                | `OPIK_MCP_PORT=8080`                     |
| `STREAMABLE_HTTP_HOST=127.0.0.1`           | `OPIK_MCP_HOST=127.0.0.1`                |
| `REMOTE_TOKEN_WORKSPACE_MAP=...`           | TBD — verify whether implemented         |
| Toolsets: `core`, `integration`, etc.      | 11-tool surface (`ask_ollie`, `read`, `list`, ...) — different model entirely |
| Cursor: `command: "npx", args: ["-y", "opik-mcp", "--apiKey", "..."]` | Cursor: `command: "uvx", args: ["opik-mcp"], env: { "OPIK_API_KEY": "..." }` |
| VS Code: `type: "stdio", command: "npx", args: ["-y", "opik-mcp", "--apiKey", "${input:...}"]` | VS Code: `type: "stdio", command: "uvx", args: ["opik-mcp"], env: { "OPIK_API_KEY": "${input:...}" }` |

This guide is the single most important deliverable for existing v2 users. Without it, they hit a wall on the first `uvx` invocation because the auth model moved from CLI flag to env var.

**Confirm with engineering before publishing:** the env-var names listed above for the Python side are inferred from `__main__.py` and the Python README. The Phase-1 PoC may not yet implement all of them (e.g., `OPIK_TOOLSETS`, `REMOTE_TOKEN_WORKSPACE_MAP` equivalents). The guide must reflect *what's actually shipped in v0.1.0*, not what existed in TS v2.

---

## Repo metadata files to update (rev 2)

The v1 plan handled `package.json`, `pyproject.toml`, `Makefile`, `.gitignore`, and `README.md`. The following metadata files also need attention:

### `.github/labeler.yml` — full rewrite

The current globs are entirely TS-pathed and will silently misfire after the move. Replacement:

```yaml
python:
  - changed-files:
    - any-glob-to-any-file:
      - 'src/opik_mcp/**'
      - 'pyproject.toml'
      - 'uv.lock'

legacy-typescript:
  - changed-files:
    - any-glob-to-any-file:
      - 'legacy/typescript/**'

security:
  - changed-files:
    - any-glob-to-any-file:
      - 'src/opik_mcp/config.py'
      - 'src/opik_mcp/server.py'
      - 'src/opik_mcp/comet_client.py'
      - 'src/opik_mcp/opik_client.py'
      - 'src/opik_mcp/ollie_client.py'
      - 'docs/auth-flow.md'

dependencies:
  - changed-files:
    - any-glob-to-any-file:
      - 'pyproject.toml'
      - 'uv.lock'
      - 'legacy/typescript/package.json'
      - 'legacy/typescript/package-lock.json'

infrastructure:
  - changed-files:
    - any-glob-to-any-file:
      - '.github/**/*'
      - 'legacy/typescript/Dockerfile'
      - 'Makefile'

documentation:
  - changed-files:
    - any-glob-to-any-file:
      - 'README.md'
      - 'docs/**/*'
      - '**/*.md'

tests:
  - changed-files:
    - any-glob-to-any-file:
      - 'tests/**/*'
      - 'legacy/typescript/tests/**/*'
```

### `.github/CODEOWNERS` — clean up dangling reference

The file references `.github/release-drafter.yml` which does not exist in the repo. Either:
- Add a real release-drafter config (recommended — gives you auto-drafted GitHub releases), or
- Remove the line.

If adding release-drafter, also add a workflow `.github/workflows/release-drafter.yml` and pin label-driven categorization to the new `python` / `legacy-typescript` labels from `labeler.yml`.

### `.github/pull_request_template.md`

Currently generic enough to survive the move. One optional improvement: add a checkbox row that prompts contributors to indicate whether the change touches `legacy/typescript/` or root Python.

### `server.json` — MCP Registry strategy

Three options, decision required:

1. **Extend existing `legacy/typescript/server.json` `packages[]`** to include both npm and PyPI entries. Single registry record, advertises both runtimes. *Pro:* one source of truth. *Con:* mixes deprecated TS + active Python under the same record; consumers can't tell which is canonical.
2. **Create a new root `server.json` for the Python package**; leave `legacy/typescript/server.json` for the npm package (and stop publishing it to the registry after v2.0.2). *Pro:* clean separation. *Con:* the MCP Registry doesn't support two `io.github.comet-ml/opik-mcp` records — name collision.
3. **Rename in the registry.** Publish Python as `io.github.comet-ml/opik-mcp` (root `server.json`) and migrate the npm entry to `io.github.comet-ml/opik-mcp-legacy` (so it remains discoverable but clearly secondary). *Pro:* matches the deprecation story end-to-end. *Con:* requires re-publishing the npm record under a new identifier, breaks existing inbound links from the MCP Registry. **Recommended.**

Whatever path you pick, the root `server.json` for the Python package looks roughly like:

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-10-17/server.schema.json",
  "name": "io.github.comet-ml/opik-mcp",
  "title": "Opik MCP Server",
  "description": "MCP server for Opik prompts, traces, datasets, metrics, and Ollie.",
  "version": "0.1.0",
  "websiteUrl": "https://www.comet.com/site/products/opik/",
  "repository": { "source": "github", "url": "https://github.com/comet-ml/opik-mcp" },
  "packages": [
    {
      "registryType": "pypi",
      "registryBaseUrl": "https://pypi.org",
      "identifier": "opik-mcp",
      "version": "0.1.0",
      "transport": { "type": "stdio" },
      "runtimeHint": "uvx",
      "environmentVariables": [
        { "name": "OPIK_API_KEY", "description": "Opik API key.", "isRequired": true, "isSecret": true },
        { "name": "OPIK_URL_OVERRIDE", "description": "Self-hosted Opik base URL.", "placeholder": "http://localhost:5173" },
        { "name": "COMET_WORKSPACE", "description": "Comet workspace name for ask_ollie." }
      ]
    }
  ]
}
```

The published env-var list must match what `opik_mcp.config:get_settings()` actually reads — sync with engineering before submitting.

### README badges (root, Python)

Replace the legacy README's Node/TS badges with:

```markdown
[![License](https://img.shields.io/github/license/comet-ml/opik-mcp)](./LICENSE)
[![PyPI - Version](https://img.shields.io/pypi/v/opik-mcp)](https://pypi.org/project/opik-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/opik-mcp)](https://pypi.org/project/opik-mcp/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/opik-mcp)](https://pypi.org/project/opik-mcp/)
[![MCP Enabled](https://badge.mcpx.dev?status=on)](https://modelcontextprotocol.io/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.15411156.svg)](https://doi.org/10.5281/zenodo.15411156)
```

The DOI badge can stay; Zenodo will issue a new minor DOI for the next release but the concept DOI (`15411156`) keeps resolving.

### CITATION.cff

Leave the existing file in place. After the first Python release, optionally cut a new Zenodo release so the citation reflects v0.1.0; the concept DOI remains stable, only a `version` DOI gets added.

---

## GitHub repo settings to update post-merge

Manual UI steps. Not part of the migration commit.

1. **Settings → Branches → main → Branch protection rule**: the required status check named `ci` (from the old `ci.yml`) no longer exists. Update to require both `Python CI` and `Legacy TS CI` (or just `Python CI` if you accept that legacy-touching PRs can land without their legacy CI being required).
2. **Settings → Environments**: ensure `pypi` and `testpypi` environments exist for Trusted Publishing (Phase 7.2). Add required reviewers on `pypi` if production publishes should be gated.
3. **About panel** (repo home page, right sidebar pencil icon):
   - Description: "Python MCP server for Opik observability and Ollie agent integration."
   - Topics: add `python`, `pypi`, `uvx`; keep `mcp`, `opik`, `llm`; drop `typescript` and `nodejs` if present.
   - Homepage: update to PyPI page (`https://pypi.org/project/opik-mcp/`) after first publish.
4. **Releases page**: archive the current "v2.0.1" release as the final TS release (already done by the `ts-v2.0.1-final` tag from Phase pre-flight); future releases tagged `py-v*` will auto-appear.
5. **`.github/workflows` page**: confirm the renamed legacy workflows still appear; old "CI" workflow runs from previous PRs will remain in history but not re-run.

---

## CI changes (detailed)

### Rename + edit `ci.yml` → `legacy-ts-ci.yml`

```yaml
name: Legacy TS CI

on:
  pull_request:
    branches: [ main ]
    paths:
      - 'legacy/typescript/**'
      - '.github/workflows/legacy-ts-ci.yml'

jobs:
  ci:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: legacy/typescript
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '18'
          cache: 'npm'
          cache-dependency-path: legacy/typescript/package-lock.json
      - run: npm ci
      - run: make precommit
      - run: npm run build
```

### Rename + edit `deploy.yml` → `legacy-ts-deploy.yml`

Tag scheme for TS releases stays `v*` for backward compatibility, but we restrict the workflow to tags that match the existing pattern. Add `working-directory: legacy/typescript` to all `run:` steps that operate on npm files, and update the `server.json` `jq` invocation to use the new path.

Key changes:
```yaml
on:
  push:
    tags:
      - "v2.*"        # narrow to v2.x.x — TS major line
      - "v3.*"        # if you ever cut a v3 from TS (unlikely; document the convention)
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: legacy/typescript
    # ... rest identical, with the jq step operating on legacy/typescript/server.json
```

Decision required during execution: **do you ever cut another TS v2 release?** If yes, keep this workflow live. If no, archive the workflow (rename to `.disabled`) and rely on `ts-v2.0.1-final` as the permanent snapshot.

### Add `python-ci.yml`

```yaml
name: Python CI

on:
  pull_request:
    branches: [ main ]
    paths-ignore:
      - 'legacy/**'

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: 'latest'
      - run: uv python install 3.13
      - run: uv sync --extra dev
      - run: make check    # lint + typecheck + test
```

### Add `python-release.yml` (PyPI publish)

Triggered by `py-v*` tags so it can't collide with TS `v*` tags. Trusted publishing via PyPI OIDC (no API token needed once you configure the project on PyPI).

**See Phase 7.3 for the full workflow** — it includes both TestPyPI and production PyPI jobs, gated by environments. The minimal v1 version below is superseded.

<details>
<summary>Minimal v1 workflow (superseded by Phase 7.3 — kept for reference)</summary>

```yaml
name: Python Release

on:
  push:
    tags:
      - "py-v*"
  workflow_dispatch:

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install 3.13
      - run: uv sync --extra dev
      - run: uv build
      - uses: pypa/gh-action-pypi-publish@release/v1
```
</details>

### Update `.github/dependabot.yml`

```yaml
version: 2
updates:
  # Python (root)
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 10
    labels: ["dependencies", "python"]

  # Legacy TypeScript
  - package-ecosystem: "npm"
    directory: "/legacy/typescript"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    labels: ["dependencies", "legacy-typescript"]
```

Note: Dependabot does not yet have first-class `uv` support (as of 2025). If `pip` ecosystem with `pyproject.toml` causes false positives or misses, switch to Renovate or pin manually. The `pip` ecosystem reads PEP 621 `[project.dependencies]` from `pyproject.toml` and works for most basic cases.

---

## Root files to rewrite

### Root `.gitignore` (merged)

Start from the Python `.gitignore`, append the TypeScript build paths scoped to `legacy/typescript/`:

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
env/
.uv/
dist/
build/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# IDE
.vscode/
.idea/
*.swp
*.code-workspace

# OS
.DS_Store
Thumbs.db

# Env
.env
.env.local
.env.*.local

# Logs
*.log
logs/
*.lcov
coverage/

# Misc
.tmp
.temp

# Legacy TypeScript (scoped paths)
legacy/typescript/node_modules/
legacy/typescript/build/
legacy/typescript/dist/
legacy/typescript/*.tsbuildinfo
legacy/typescript/coverage/
legacy/typescript/.cursor/mcp.json
```

### Root `README.md` (key sections)

Use the Python repo README as the base, then **prepend** the legacy callout (see Phase 3, step 1). Also update any internal links — the Python README references `docs/team-brief.md`, `docs/design.md`, etc., which are all preserved when we copy `docs/` over.

---

## Tagging convention (post-migration)

| What you ship          | Tag pattern        | Workflow                  | Registry       |
| ---------------------- | ------------------ | ------------------------- | -------------- |
| TS legacy release      | `v2.x.y`           | `legacy-ts-deploy.yml`    | npm `opik-mcp` |
| Python release         | `py-v0.x.y`        | `python-release.yml`      | PyPI `opik-mcp` |
| Pre-migration snapshot | `ts-v2.0.1-final`  | (none — anchor only)      | —              |

Rationale: prefixing Python tags avoids accidentally retriggering the legacy npm deploy when cutting a Python release.

---

## Verification checklist

Run all of these from a fresh clone of the migration branch before merging.

### Python (root)

- [ ] `uv sync --extra dev` succeeds
- [ ] `make lint` passes
- [ ] `make typecheck` passes
- [ ] `make test` passes
- [ ] `uv run opik-mcp --help` produces output (or the server starts via `make run`)
- [ ] Importing `opik_mcp` from a fresh Python 3.13 env works

### Legacy TypeScript (`legacy/typescript/`)

- [ ] `cd legacy/typescript && npm ci` succeeds
- [ ] `cd legacy/typescript && npm run build` produces `build/` with executable `cli.js`
- [ ] `cd legacy/typescript && npm test` passes
- [ ] `cd legacy/typescript && npm run start:stdio` boots
- [ ] From repo root: `make legacy-build` works
- [ ] `legacy/typescript/server.json`'s package path still resolves (`build/cli.js` is relative to `legacy/typescript/`, which is correct as long as npm publish runs with `working-directory: legacy/typescript`)

### CI

- [ ] Open a draft PR with a no-op edit to `src/opik_mcp/server.py` → only `Python CI` runs
- [ ] Open a draft PR with a no-op edit to `legacy/typescript/src/index.ts` → only `Legacy TS CI` runs
- [ ] Open a no-op PR touching both paths → labeler applies `python` AND `legacy-typescript` labels (validates the new `labeler.yml`)
- [ ] Push a throwaway `v2.0.2-test` tag from the branch → confirm `legacy-ts-deploy.yml` triggers; delete the tag without publishing (or use `workflow_dispatch` dry-run via `npm publish --dry-run`)
- [ ] Push a throwaway `py-v0.0.0-test` tag → confirm `python-release.yml` triggers; same dry-run safety

### PyPI / `uvx` (Phase 7 deliverable)

- [ ] `pyproject.toml` includes `urls`, `keywords`, `classifiers`, license-files (review against 7.1)
- [ ] `uv build` produces `dist/opik_mcp-0.1.0-*.whl` and `dist/opik_mcp-0.1.0.tar.gz` without warnings
- [ ] `uv run twine check dist/*` returns "PASSED" for both artifacts
- [ ] Wheel contains `opik_mcp/__main__.py`, `opik_mcp/server.py`, etc. — verified via `unzip -l dist/*.whl`
- [ ] TestPyPI publish succeeds via `workflow_dispatch` with `target=testpypi`
- [ ] From a clean `python:3.11-slim` container: `pip install --index-url https://test.pypi.org/simple/ opik-mcp==0.1.0` succeeds and `opik-mcp` is on PATH
- [ ] From a clean container: `uvx --index-url https://test.pypi.org/simple/ opik-mcp@0.1.0` boots in stdio mode and accepts a `tools/list` JSON-RPC message on stdin
- [ ] Production PyPI publish via `py-v0.1.0` tag succeeds and the project is visible at https://pypi.org/project/opik-mcp/
- [ ] `uvx opik-mcp` works from a clean machine without any registry override
- [ ] PyPI project page renders the README correctly (no broken images, no raw markdown)

### Repo hygiene

- [ ] `git log --follow legacy/typescript/src/index.ts` shows history back through `de5799a` (proves `git mv` preserved history)
- [ ] Root README's legacy callout is visible on GitHub's rendered view
- [ ] `legacy/typescript/DEPRECATED.md` is reachable from the root README in one click
- [ ] `.github/labeler.yml` was rewritten (not just left at the TS-era globs)
- [ ] `CODEOWNERS` no longer references missing files

---

## Post-migration tasks (separate PRs, not part of the migration commit)

These follow the migration merge and the Phase 7 PyPI publish.

1. **npm `deprecate` notice on stale TS versions.** Once Python is on PyPI and verified working:
   ```bash
   npm deprecate opik-mcp@"<2.0.1" "Use Python: uvx opik-mcp. See https://github.com/comet-ml/opik-mcp"
   ```
   Leave `2.0.1` undeprecated for now since it's still the supported legacy. Consider `npm deprecate opik-mcp@"2.0.1" "..."` once the Python server hits feature parity and the announced EOL window closes.
2. **Per-host MCP config snippets.** Write `docs/install/cursor.md`, `docs/install/vscode.md`, `docs/install/windsurf.md`, `docs/install/claude-code.md` with `uvx`-based examples (see Phase 8 mapping table). Currently `docs/install/README.md` is a placeholder.
3. **CHANGELOG.md** at root, Keep-a-Changelog format. Seed it with: "0.1.0 — initial PyPI release. Migrated from TypeScript implementation now under `legacy/typescript/` (npm `opik-mcp@2.0.1`)."
4. **Zenodo release for v0.1.0.** Trigger a new Zenodo release so the citation entry tracks the Python implementation. Concept DOI stays at `15411156`; a version DOI gets minted.
5. **Move `docs/migration-plan.md` to `docs/decisions/0006-typescript-to-python-migration.md`** as an ADR once the migration completes, so it's filed with the other architectural decisions in the Python repo's ADR directory.
6. **Announce on community channels.** docs site notice on https://www.comet.com/docs/opik/prompt_engineering/mcp_server, Slack community, Twitter/X, GitHub Discussions, Opik Discord. Coordinate with marketing/devrel.
7. **Functional parity tracker.** Open a tracking issue listing every TS v2.0.1 tool/toolset/transport/env var and whether the Python v0.1.0 implements it. Drives the v0.2.0+ roadmap.

---

## Rollback

If the migration goes wrong after merge:

```bash
# On main
git revert -m 1 <merge-commit-sha>
# Or, hard rollback (only if no other commits landed after):
git reset --hard ts-v2.0.1-final
git push --force-with-lease origin main   # requires confirmation from a maintainer
```

The `ts-v2.0.1-final` tag is the safety anchor — never delete it.

---

## Open questions to resolve before executing

1. **EOL date for TS server.** Fill in `legacy/typescript/DEPRECATED.md` — proposed: 12 months after Python `v0.1.0` PyPI release, security patches only during that window.
2. **Cut TS v2.0.2?** Decide whether the current `de5799a` state needs one more npm release with a deprecation notice in the README before going maintenance-only.
3. **Smithery.** `legacy/typescript/smithery.yaml` currently deploys the TS server to Smithery's hosted MCP catalog. Confirm with whoever maintains the Smithery listing whether we keep deploying it (path-fixed) or pull it. Smithery also supports `uvx`-based servers — separate decision on whether to list the Python distribution there.
4. **MCP Registry.** Choose one of the three options in "Repo metadata files to update → `server.json`" above. **Recommendation: Option 3** (Python takes the canonical `io.github.comet-ml/opik-mcp` name; legacy TS gets `-legacy` suffix).
5. **PyPI project ownership.** Which Comet PyPI account claims `opik-mcp` on first publish? The account must have 2FA enabled and become the project owner before Trusted Publishing can be configured.
6. **`requires-python` floor.** Python repo currently pins `>=3.13`. Drop to `>=3.11` to widen the user base? Requires a quick CI matrix run on 3.11 / 3.12 to confirm the codebase actually works there (no PEP 695 generics, no `defer`, etc.).
7. **Env-var surface confirmation.** Phase 8's migration table lists env vars that may or may not be wired in v0.1.0 (e.g., `OPIK_TOOLSETS`, remote auth token map). Audit `opik_mcp.config:get_settings()` against the table and only ship the guide with verified entries.
8. **Initial Python version number.** Plan assumes `0.1.0` for the first PyPI release. If you want to signal "beta but production-intent", that's right. If you want to signal "stable from day one", consider `1.0.0`. If the codebase is genuinely PoC-quality at first publish, `0.0.1` or `0.1.0a1` (alpha) is more honest.
9. **CHANGELOG.md.** No changelog file in either repo today. Add one at root before first PyPI release? Keep a Changelog format is the de-facto standard.
10. **Pre-commit hooks.** TS uses `pre-commit` npm package. Python has nothing. Set up `.pre-commit-config.yaml` with `ruff`, `mypy` for parity, or rely on `make check` in CI only?
11. **Python Dockerfile.** TS has one in `legacy/typescript/Dockerfile`. Should we add a root `Dockerfile` for users who want to self-host the Python server in a container, or is `uvx` sufficient for the supported install paths?

---

## What this plan does NOT cover

Out of scope for the migration commit itself, but worth flagging so they don't get forgotten:

- **Functional parity audit.** Whether the Python v0.1.0 actually exposes every tool/transport the TS v2.0.1 did is a separate engineering question. The migration moves files; it doesn't verify behavior.
- **Hosted remote MCP.** The TS README says "we do not currently provide a hosted remote MCP service for Opik." Phase 2 of the Python repo plans to host at `https://www.comet.com/api/v1/mcp` — that's its own project, not this migration.
- **Telemetry / Sentry hookup** in the new Python server.
- **Translation of `docs/streamable-http-transport.md`, `docs/configuration.md`, `docs/api-reference.md`** from TS specifics to Python specifics. The originals stay in `legacy/typescript/docs/` so users on v2 still have them; equivalents for Python need writing fresh.
- **Notifying existing users.** Posting a deprecation notice on https://www.comet.com/docs/opik/prompt_engineering/mcp_server, Slack community, Twitter, the npm package's "deprecated" message, the GitHub Discussions board, and the Opik Discord (if it exists) is a separate comms task.

# Opik MCP Uplift Plan (Branches + Worktrees)

## Objectives
- Modernize transport and protocol compliance for remote MCP usage.
- Add security safeguards for safe remote deployment.
- Align repository governance and GitHub Actions safeguards with patterns used in `comet-ml/opik`.
- Deliver changes in small, reviewable PRs with low merge risk.

## Ground Rules
- Keep `main` stable and releasable at all times.
- Use one concern per branch/PR.
- Rebase each branch on current `main` before opening PR.
- Merge order is strict because later branches depend on earlier ones.

## Branch + Worktree Layout
Create sibling worktrees so each stream can progress independently.

```bash
# from repo root: /Users/vincentkoc/GIT/_Comet/opik-mcp
mkdir -p ../_wt

# 0) governance and templates first
git worktree add ../_wt/opik-mcp-governance -b vincentkoc-code/governance-parity main

# 1) transport modernization
git worktree add ../_wt/opik-mcp-transport -b vincentkoc-code/transport-streamable-http main

# 2) security hardening (depends on transport)
git worktree add ../_wt/opik-mcp-security -b vincentkoc-code/security-safeguards main

# 3) ci/actions hardening (can start from main, rebase before merge)
git worktree add ../_wt/opik-mcp-actions -b vincentkoc-code/actions-parity main

# 4) docs + migration playbook (after transport + security)
git worktree add ../_wt/opik-mcp-docs -b vincentkoc-code/docs-remote-migration main
```

## Merge Order
1. `vincentkoc-code/governance-parity`
2. `vincentkoc-code/transport-streamable-http`
3. `vincentkoc-code/security-safeguards`
4. `vincentkoc-code/actions-parity`
5. `vincentkoc-code/docs-remote-migration`

## Detailed Scope By Branch

## 1) Governance Parity
Goal: add repo guardrails similar to `comet-ml/opik` but sized to this repo.

Planned files:
- `.github/CODEOWNERS`
- `.github/dependabot.yml`
- `.github/release-drafter.yml`
- `.github/labeler.yml`
- `.github/pull_request_template.md` (refresh structure and checks)
- `.github/ISSUE_TEMPLATE/*` (bug/feature/security)

Notes:
- Use `opik` templates as baseline, then simplify ownership and path rules for this single-package repo.
- Keep labels relevant to MCP server concerns (`transport`, `security`, `docs`, `infra`, `dependencies`).

Acceptance:
- PRs auto-labeled.
- Dependabot opens weekly updates.
- Release draft updates on `main`.
- CODEOWNERS required-review pathing works.

## 2) Transport Streamable HTTP
Goal: make remote usage standards-compliant.

Implementation:
- Upgrade to latest `@modelcontextprotocol/sdk`.
- Add first-class `StreamableHTTPServerTransport` endpoint (e.g. `/mcp`).
- Add session management (`mcp-session-id`) for stateful mode.
- Keep legacy SSE only as compatibility fallback (clearly marked deprecated), or remove if not needed.
- Remove custom broadcast transport behavior that leaks responses across clients.
- Refactor server bootstrap so import side effects are removed (explicit `startServer()` path).

Tests:
- Add transport tests for initialize, session reuse, bad session handling, and concurrent clients.
- Validate interoperability with SDK streamable-http client.

Acceptance:
- Remote MCP client can initialize and call tools over Streamable HTTP.
- No cross-client response leakage.
- Backward compatibility decision is explicit and tested.

## 3) Security Safeguards
Goal: safe-by-default remote deployment.

Implementation:
- Add explicit auth mode for remote HTTP transport:
  - bearer token check (minimum)
  - optional allowlist for origins/hosts
- Harden CORS:
  - explicit `origin` allowlist
  - expose `Mcp-Session-Id`
  - allow `mcp-session-id` request header
- Add read-only mode (`--read-only`, env equivalent).
- Add allowed project scoping (`--allowed-projects`, env equivalent).
- Add request body size limits and basic rate limiting for HTTP transport.

Tests:
- auth required/forbidden cases
- read-only mutation blocking
- project scoping enforcement
- CORS header behavior for browser clients

Acceptance:
- Unauthorized requests blocked.
- Mutations blocked in read-only mode.
- Out-of-scope project access blocked.

## 4) Actions Parity (Adapted)
Goal: bring key CI safeguards from main `opik` repo without overfitting.

Add workflows:
- `.github/workflows/pr-lint.yml` (title/description/issue linkage checks)
- `.github/workflows/pr-auto-assign.yml`
- `.github/workflows/labeler.yml` (uses `.github/labeler.yml`)
- `.github/workflows/release-drafter.yml`

Update existing workflows:
- split CI into focused jobs (lint, test, build) with explicit permissions and concurrency.
- preserve existing publish workflow but add preflight checks and least-privilege permissions.

Repository settings to enable (manual in GitHub UI):
- branch protection on `main`
- require PR reviews
- require status checks from new workflows
- require CODEOWNERS review
- restrict direct pushes

Acceptance:
- New PR lifecycle automations run and pass.
- Main branch protections can safely rely on these checks.

## 5) Docs + Migration Playbook
Goal: make adoption clear and reduce support burden.

Deliverables:
- Update README transport section to Streamable HTTP first.
- Replace/retire outdated custom SSE docs.
- Add `docs/remote-deployment.md` with secure examples:
  - reverse proxy/TLS
  - auth token configuration
  - CORS/session header requirements
- Add migration notes for old SSE users.

Acceptance:
- New user can configure remote server from docs only.
- Deprecated paths are clearly marked with cutoff guidance.

## Safeguards/Actions Parity Matrix
Reference source: `comet-ml/opik/.github`.

Adopt now:
- `CODEOWNERS`
- `dependabot.yml`
- `release-drafter.yml`
- `labeler.yml` + workflow
- `pr-lint.yml`
- `pr-auto-assign.yml`
- issue/PR templates

Adapt (not copy 1:1):
- large multi-app test matrices from main `opik` repo
- language-specific workflows unrelated to this TypeScript MCP package

Defer:
- heavyweight E2E matrices until streamable HTTP stabilizes.

## Execution Checklist
- [ ] Create worktrees/branches.
- [ ] Ship governance parity PR.
- [ ] Ship transport modernization PR.
- [ ] Ship security safeguards PR.
- [ ] Ship actions parity PR.
- [ ] Ship docs/migration PR.
- [ ] Enable/verify branch protection rules and required checks.
- [ ] Cut release candidate and run remote smoke tests.

## Remote Smoke Test Gate (before release)
- initialize over Streamable HTTP succeeds.
- tool listing and representative tool call succeed.
- concurrent client isolation verified.
- unauthorized token rejected.
- read-only mode blocks writes.
- allowed-projects denies out-of-scope project.
- publish pipeline dry run succeeds.

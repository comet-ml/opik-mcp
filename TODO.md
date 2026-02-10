# TODO - Governance Parity

Branch: `vincentkoc-code/governance-parity`

## Scope
- Add governance and contribution guardrails aligned with `comet-ml/opik`, adapted for this single-package MCP repo.

## Tasks
- [ ] Add `.github/CODEOWNERS` with maintainers and docs/workflow ownership.
- [ ] Add `.github/dependabot.yml` (npm ecosystem for root).
- [ ] Add `.github/release-drafter.yml`.
- [ ] Add `.github/labeler.yml` with MCP-relevant label mapping.
- [ ] Refresh `.github/pull_request_template.md`.
- [ ] Add issue templates:
  - [ ] `.github/ISSUE_TEMPLATE/bug_report.yml`
  - [ ] `.github/ISSUE_TEMPLATE/feature_request.yml`
  - [ ] `.github/ISSUE_TEMPLATE/security.yml`

## Acceptance
- [ ] New PRs can be auto-labeled by file changes.
- [ ] Dependabot config validates in GitHub.
- [ ] CODEOWNERS paths match repo layout.

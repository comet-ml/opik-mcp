# Contributing to Opik MCP

Thanks for contributing to `opik-mcp`.

This guide is scoped to this repository. For broader Opik project contribution guidance, see the main Opik guide:

- [comet-ml/opik CONTRIBUTING.md](https://github.com/comet-ml/opik/blob/main/CONTRIBUTING.md)
- [Contributor License Agreement (CLA)](https://github.com/comet-ml/opik/blob/main/CLA.md)

## Before opening an issue

1. Search existing issues first: <https://github.com/comet-ml/opik-mcp/issues>
2. Use the matching template:
   - Bug report: [.github/ISSUE_TEMPLATE/bug_report.yml](.github/ISSUE_TEMPLATE/bug_report.yml)
   - Feature request: [.github/ISSUE_TEMPLATE/feature_request.yml](.github/ISSUE_TEMPLATE/feature_request.yml)
3. Include reproducible steps, configuration, and environment details.

## Local setup

Prerequisites:

- Node.js `>=20.11.0`
- npm

Clone and bootstrap:

```bash
git clone https://github.com/comet-ml/opik-mcp.git
cd opik-mcp
npm install
npm run build
```

Optional runtime config:

```bash
cp .env.example .env
```

## Development workflow

1. Create a branch for your change.
2. Keep changes focused and avoid unrelated formatting-only edits.
3. Run local checks before opening/updating a PR.

Recommended local checks:

```bash
npm run lint
npm test
npm run build
```

Useful extras:

```bash
# all tests + lint
npm run check

# transport-focused tests
npm run test:transport

# pre-commit equivalent checks
make precommit
```

## Pull requests

Open PRs here: <https://github.com/comet-ml/opik-mcp/pulls>

Please:

1. Prefer a draft PR early for feedback.
2. Follow the PR template: [.github/pull_request_template.md](.github/pull_request_template.md)
3. Link related issues using `Fixes #<issue-number>` (or `Resolves #<issue-number>`) in the PR body.
4. Update tests and docs for behavior changes.
5. Call out breaking changes clearly.

If you use GitHub CLI, common commands are:

```bash
gh pr create --draft
gh pr view --web
```

## Commit and review expectations

- Keep commits scoped and reviewable.
- Use clear commit messages that describe behavior change.
- If changing public behavior (tools, transport, config), update relevant docs in `README.md` or `docs/`.

## Security and secrets

- Do not commit API keys, tokens, or `.env` files.
- Use `.env.example` as the template for new configuration fields.

## Questions and support

- Community chat: <https://chat.comet.com>
- Opik docs: <https://www.comet.com/docs/opik>

# TypeScript Opik MCP server — deprecated

This implementation is in maintenance-only mode. The Python implementation at the repo root is the supported version going forward.

- **Last feature release:** `v2.0.1` (npm `opik-mcp@2.0.1`)
- **Security-patch policy:** critical CVEs only, until **2026-11-15**
- **End of life:** **2026-11-15**
- **Migration:** install via `uvx opik-mcp` instead of `npx -y opik-mcp`. Tools, transports, and config env vars are renamed/restructured — see the root [`README.md`](../../README.md) and `docs/` for the new surface.
- **Releases:** publish by dispatching `.github/workflows/legacy-ts-deploy.yml` from an `npm-v*` tag (e.g. `npm-v2.0.2`) on a branch off `legacy-typescript-final`. The workflow stays on `main` until the end of life so the dispatch exists.

The TypeScript source was removed from `main` on 2026-09-24. It lives at the
git tag `legacy-typescript-final`. To work on a sunset release:

```bash
git checkout -b ts-sunset legacy-typescript-final
cd legacy/typescript
npm install
npm run build
npm test
```

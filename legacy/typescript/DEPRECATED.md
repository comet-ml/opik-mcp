# TypeScript Opik MCP server — deprecated

This implementation is in maintenance-only mode. The Python implementation at the repo root is the supported version going forward.

- **Last feature release:** `v2.0.1` (npm `opik-mcp@2.0.1`)
- **Security-patch policy:** critical CVEs only, until **2026-11-15**
- **End of life:** **2026-11-15**
- **Migration:** install via `uvx opik-mcp` instead of `npx -y opik-mcp`. Tools, transports, and config env vars are renamed/restructured — see the root [`README.md`](../../README.md) and `docs/` for the new surface.
- **Release tag prefix:** patches to this package are published from tags matching `npm-v*` (e.g. `npm-v2.0.2`); the Python package uses `py-v*`. Plain `v*` tags do **not** trigger any release workflow.

The TypeScript code remains buildable and testable in place:

```bash
cd legacy/typescript
npm install
npm run build
npm test
```

Or from the repo root via `make legacy-install`, `make legacy-build`, etc.

The `legacy/typescript/README.md` is preserved verbatim from the v2.0.1 release for reference; it does not reflect that the package is deprecated. This file is the authoritative deprecation notice.

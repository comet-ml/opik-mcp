/**
 * Single source of truth for the deprecation message surfaced by every
 * channel (MCP ``instructions`` field, stderr banner, README, package
 * description, future tool-result injection). Keep all wording here so
 * the four channels never drift apart.
 *
 * The companion `legacy/typescript/DEPRECATED.md` declares the same EOL
 * date — update both together if it changes.
 */

export const SUNSET_DATE = '2026-11-15' as const;
export const MIGRATION_URL =
  'https://github.com/comet-ml/opik-mcp/blob/main/legacy/typescript/MIGRATION.md' as const;

const ONE_LINE =
  `[DEPRECATED] opik-mcp (npm) is deprecated. Migrate to: ` +
  `uvx opik-mcp@latest — sunset ${SUNSET_DATE}.`;

const SHORT =
  `The TypeScript Opik MCP server is deprecated and will stop ` +
  `serving requests on ${SUNSET_DATE}. ` +
  `Install the supported Python build with: uvx opik-mcp@latest`;

const FULL = `\
============================================================
  ⚠️  opik-mcp (npm, TypeScript) is DEPRECATED
============================================================

This server stops serving MCP requests on ${SUNSET_DATE}.

Migrate now — in your MCP client config, replace:

  npx -y opik-mcp

with:

  uvx opik-mcp@latest

The tool surface, env vars and transports have changed. Migration
guide: ${MIGRATION_URL}

============================================================`;

export const DEPRECATION_NOTICE = {
  oneLine: ONE_LINE,
  short: SHORT,
  full: FULL,
  sunsetDate: SUNSET_DATE,
  migrationUrl: MIGRATION_URL,
} as const;

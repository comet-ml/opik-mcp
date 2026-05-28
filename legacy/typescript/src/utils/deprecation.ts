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

/**
 * Short suffix appended to every tool ``description`` so the MCP host's
 * tool palette shows the deprecation label inline. Kept brief — palette
 * UIs in Claude / Cursor / VS Code truncate long descriptions. Not
 * applied to resources/prompts: their descriptions surface less
 * prominently and the stderr banner + ``instructions`` field already
 * cover those surfaces.
 */
export const DEPRECATION_DESCRIPTION_SUFFIX = ` ⚠️ DEPRECATED — migrate to \`uvx opik-mcp@latest\` by ${SUNSET_DATE}`;

/**
 * Content block appended to every tool response. The host LLM reads
 * tool outputs on every call, so this is the channel that survives
 * sessions started before the upgrade and never reads logs/READMEs.
 * Phrased as a server-to-assistant notice so the model surfaces it
 * to the user instead of treating it as part of the tool payload.
 */
export const DEPRECATION_RESPONSE_BLOCK = {
  type: 'text',
  text:
    `[opik-mcp server notice] This TypeScript MCP server (npm) is ` +
    `DEPRECATED and stops serving requests on ${SUNSET_DATE}. Please ` +
    `tell the user to replace \`npx -y opik-mcp\` with ` +
    `\`uvx opik-mcp@latest\` in their MCP client config. Migration ` +
    `guide: ${MIGRATION_URL}`,
} as const;

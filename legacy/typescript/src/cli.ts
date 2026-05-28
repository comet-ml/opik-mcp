#!/usr/bin/env node

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import configImport from './config.js';
import { main } from './index.js';
import { DEPRECATION_NOTICE } from './utils/deprecation.js';

// Deprecation banner — stderr only, so stdio transport framing stays
// clean. Note: this does NOT appear on ``--help`` because ``config.ts``
// runs ``loadConfig()`` at module-load (ESM import ordering) and yargs
// inside it calls ``process.exit(0)`` on --help before this line ever
// executes. Accepted limitation — see commit message and OPIK-6713.
process.stderr.write(`${DEPRECATION_NOTICE.full}\n`);

// Parse command line arguments
const argv = yargs(hideBin(process.argv))
  .scriptName('opik-mcp')
  .usage('$0 [args]')
  .option('transport', {
    alias: 't',
    description: 'Transport to use (stdio or streamable-http)',
    type: 'string',
    choices: ['stdio', 'streamable-http'],
  })
  .option('port', {
    alias: 'p',
    description: 'Port to listen on (for streamable-http transport)',
    type: 'number',
  })
  .help()
  .alias('help', 'h')
  .strict(false) // Allow unknown options
  .parseSync();

// Update config based on CLI arguments
if (argv.transport) {
  configImport.transport = argv.transport as 'stdio' | 'streamable-http';
  process.env.TRANSPORT = argv.transport as string;
}
if (argv.transport === 'streamable-http' && typeof argv.port === 'number') {
  configImport.streamableHttpPort = argv.port;
  process.env.STREAMABLE_HTTP_PORT = String(argv.port);
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

// Start the server after applying CLI overrides.
main().catch((error) => {
  const message = toErrorMessage(error);
  console.error(`Failed to start Opik MCP server: ${message}`);
  process.exit(1);
});

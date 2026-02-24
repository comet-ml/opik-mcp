#!/usr/bin/env node

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import configImport from './config.js';
import { main } from './index.js';

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
main().catch(error => {
  const message = toErrorMessage(error);
  console.error(`Failed to start Opik MCP server: ${message}`);
  process.exit(1);
});

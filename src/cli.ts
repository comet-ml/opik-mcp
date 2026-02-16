#!/usr/bin/env node

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import configImport from './config.js';

// Parse command line arguments
const argv = yargs(hideBin(process.argv))
  .scriptName('opik-mcp')
  .usage('$0 [args]')
  .option('transport', {
    alias: 't',
    description: 'Transport to use (stdio, sse alias, or streamable-http)',
    type: 'string',
    default: 'stdio',
    choices: ['stdio', 'sse', 'streamable-http'],
  })
  .option('port', {
    alias: 'p',
    description: 'Port to listen on (for sse transport)',
    type: 'number',
    default: 3001,
  })
  .help()
  .alias('help', 'h')
  .strict(false) // Allow unknown options
  .parseSync();

// Update config based on CLI arguments
configImport.transport = argv.transport as 'stdio' | 'sse' | 'streamable-http';
if (argv.transport === 'sse' || argv.transport === 'streamable-http') {
  configImport.ssePort = argv.port as number;
}

// Import and start the server (index.js will handle the main() call)
import './index.js';

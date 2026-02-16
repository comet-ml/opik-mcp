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
    description: 'Transport to use (stdio or streamable-http)',
    type: 'string',
    default: 'stdio',
    choices: ['stdio', 'streamable-http'],
  })
  .option('port', {
    alias: 'p',
    description: 'Port to listen on (for streamable-http transport)',
    type: 'number',
    default: 3001,
  })
  .help()
  .alias('help', 'h')
  .strict(false) // Allow unknown options
  .parseSync();

// Update config based on CLI arguments
configImport.transport = argv.transport as 'stdio' | 'streamable-http';
if (argv.transport === 'streamable-http') {
  configImport.streamableHttpPort = argv.port as number;
}

// Import and start the server (index.js will handle the main() call)
import './index.js';

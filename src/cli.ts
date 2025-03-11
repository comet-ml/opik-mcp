#!/usr/bin/env node

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { SSEServerTransport } from './transports/sse-transport.js';
import { startServerWithTransport } from './mcp-server.js';

// Parse command line arguments
const argv = yargs(hideBin(process.argv))
  .scriptName('opik-mcp')
  .usage('$0 [args]')
  .command('serve', 'Start the MCP server', yargs => {
    return yargs
      .option('transport', {
        alias: 't',
        description: 'Transport to use (stdio or sse)',
        type: 'string',
        default: 'stdio',
        choices: ['stdio', 'sse'],
      })
      .option('port', {
        alias: 'p',
        description: 'Port to listen on (for sse transport)',
        type: 'number',
        default: 3001,
      });
  })
  .demandCommand(1, 'You need to specify a command')
  .help()
  .alias('help', 'h')
  .parseSync();

/**
 * Function to start the MCP server with the specified transport
 */
async function startServer() {
  let transport;

  // Create the appropriate transport based on command line argument
  if (argv.transport === 'sse') {
    console.log(`Starting MCP server with SSE transport on port ${argv.port as number}`);
    transport = new SSEServerTransport({ port: argv.port as number });
  } else {
    console.log('Starting MCP server with stdio transport');
    transport = new StdioServerTransport();
  }

  try {
    // Start the server with the configured transport
    await startServerWithTransport(transport);

    if (argv.transport === 'sse') {
      console.log(`Server is now accessible at http://localhost:${argv.port as number}`);
      console.log(`- Health check: http://localhost:${argv.port as number}/health`);
      console.log(`- SSE events: http://localhost:${argv.port as number}/events`);
      console.log(`- Send messages: POST to http://localhost:${argv.port as number}/send`);
    }

    // Keep the process alive
    process.on('SIGINT', async () => {
      console.log('Shutting down server...');
      await transport.close();
      process.exit(0);
    });
  } catch (error) {
    console.error(`Error starting server: ${error}`);
    process.exit(1);
  }
}

// Only start the server if we're running the serve command
if (argv._[0] === 'serve') {
  startServer().catch(error => {
    console.error(`Fatal error: ${error}`);
    process.exit(1);
  });
}

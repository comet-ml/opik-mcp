import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { Transport } from '@modelcontextprotocol/sdk/shared/transport.js';
import fs from 'fs';

// Import configuration
import configImport from './config.js';
const config = configImport;

// Setup file-based logging
const logFile = '/tmp/opik-mcp.log';

// Define logging functions
function logToFile(message: string): void {
  try {
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logFile, `[${timestamp}] ${message}\n`);
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

/**
 * Create a configured MCP server instance
 * This function is used by both the CLI and the original index.ts
 */
export function createMcpServer() {
  logToFile('Creating MCP server');

  // Import all the handlers and capabilities from index.js
  // Requires a refactor of index.js to export these as separate modules

  // For now, create a minimal configuration
  const server = new McpServer(
    {
      name: config.mcpName || 'Opik MCP',
      version: config.mcpVersion || '0.0.1',
    },
    {
      capabilities: {
        // Minimal capabilities for demo
        mcp__get_server_info: {
          name: 'get_server_info',
          description: 'Get information about the Opik server configuration',
          parameter_schema: {
            type: 'object',
            additionalProperties: false,
            properties: {
              random_string: {
                type: 'string',
                description: 'Dummy parameter for no-parameter tools',
              },
            },
          },
          handler: async () => {
            return {
              content: [
                {
                  type: 'text',
                  text: `# Opik MCP Server

Server Name: ${config.mcpName || 'Opik MCP'}
Version: ${config.mcpVersion || '0.0.1'}
API Base URL: ${config.apiBaseUrl || 'Not configured'}
Self-hosted: ${config.isSelfHosted ? 'Yes' : 'No'}
Workspace: ${config.workspaceName || 'None'}

This is a minimal configuration for demo purposes.`,
                },
              ],
            };
          },
        },
      },
    }
  );

  return server;
}

/**
 * Start the MCP server with the provided transport
 */
export async function startServerWithTransport(transport: Transport) {
  logToFile('Starting server with provided transport');

  const server = createMcpServer();

  // Add explicit error handlers to the transport
  transport.onerror = error => {
    logToFile(`Transport error: ${error.message}`);
    console.error(`Transport error: ${error.message}`);
  };

  transport.onclose = () => {
    logToFile('Transport connection closed');
    console.log('Transport connection closed');
  };

  try {
    // Connect server to transport
    await server.connect(transport);

    logToFile('Opik MCP Server successfully connected and running');
    console.log('Opik MCP Server successfully connected and running');

    return server;
  } catch (error: any) {
    logToFile(`Error in server connection: ${error?.message || error}`);
    console.error(`Error in server connection: ${error?.message || error}`);
    throw error;
  }
}

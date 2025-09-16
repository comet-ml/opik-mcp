import fs from 'fs';

// Import other modules
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { makeApiRequest } from './utils/api.js';

// Import custom transports
import { SSEServerTransport } from './transports/sse-transport.js';

// Import environment variables loader - no console output
import './utils/env.js';
import { logToFile, logFile } from './utils/logging.js';

// Import tool loaders
import { loadTraceTools } from './tools/trace.js';
import { loadPromptTools } from './tools/prompt.js';
import { loadProjectTools } from './tools/project.js';
import { loadMetricTools } from './tools/metrics.js';
import { loadIntegrationTools } from './tools/integration.js';

// Import configuration
import { loadConfig } from './config.js';
const config = loadConfig();

// Only initialize log file if debug mode is enabled
if (config.debugMode) {
  try {
    fs.writeFileSync(logFile, `Opik MCP Server Started: ${new Date().toISOString()}\n`);

    // Log process info
    logToFile(`Process ID: ${process.pid}, Node Version: ${process.version}`);
    logToFile(`Arguments: ${process.argv.join(' ')}`);
    logToFile(
      `Loaded configuration: API=${config.apiBaseUrl}, Workspace=${config.workspaceName || 'None'}`
    );

    // Register error handlers
    process.on('uncaughtException', err => {
      logToFile(`UNCAUGHT EXCEPTION: ${err.message}`);
      logToFile(err.stack || 'No stack trace');
    });

    process.on('unhandledRejection', reason => {
      logToFile(`UNHANDLED REJECTION: ${reason}`);
    });

    process.on('exit', code => {
      logToFile(`Process exiting with code ${code}`);
    });
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

// Rest of imports
import { ProjectResponse } from './types.js';

// Create and configure server - no console output here
export let server = new McpServer(
  {
    name: config.mcpName,
    version: config.mcpVersion,
  },
  {
    capabilities: {
      resources: {}, // Enable resources capability
      tools: {}, // Enable tools capability
    },
  }
);

// Load tools based on enabled toolsets
logToFile(`Loading toolsets: ${config.enabledToolsets.join(', ')}`);

if (config.enabledToolsets.includes('integration')) {
  server = loadIntegrationTools(server);
  logToFile('Loaded integration toolset');
}

if (config.enabledToolsets.includes('prompts')) {
  server = loadPromptTools(server);
  logToFile('Loaded prompts toolset');
}

if (config.enabledToolsets.includes('projects')) {
  server = loadProjectTools(server);
  logToFile('Loaded projects toolset');
}

if (config.enabledToolsets.includes('traces')) {
  server = loadTraceTools(server);
  logToFile('Loaded traces toolset');
}

if (config.enabledToolsets.includes('metrics')) {
  server = loadMetricTools(server);
  logToFile('Loaded metrics toolset');
}

// Add resources to the MCP server
if (config.workspaceName) {
  // Define a workspace info resource
  server.resource('workspace-info', 'opik://workspace-info', async () => ({
    contents: [
      {
        uri: 'opik://workspace-info',
        text: JSON.stringify(
          {
            name: config.workspaceName,
            apiUrl: config.apiBaseUrl,
            selfHosted: config.isSelfHosted,
          },
          null,
          2
        ),
      },
    ],
  }));

  // Define a projects resource that provides the list of projects in the workspace
  server.resource('projects-list', 'opik://projects-list', async () => {
    try {
      const response = await makeApiRequest<ProjectResponse>('/v1/private/projects');

      if (!response.data) {
        return {
          contents: [
            {
              uri: 'opik://projects-list',
              text: `Error: ${response.error || 'Unknown error fetching projects'}`,
            },
          ],
        };
      }

      return {
        contents: [
          {
            uri: 'opik://projects-list',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    } catch (error) {
      logToFile(`Error fetching projects resource: ${error}`);
      return {
        contents: [
          {
            uri: 'opik://projects-list',
            text: `Error: Failed to fetch projects data`,
          },
        ],
      };
    }
  });
}

// ----------- SERVER CONFIGURATION TOOLS -----------

// Main function to start the server
export async function main() {
  logToFile('Starting main function');

  // Create the appropriate transport based on configuration
  let transport;
  if (config.transport === 'sse') {
    logToFile(`Creating SSEServerTransport on port ${config.ssePort}`);
    transport = new SSEServerTransport({
      port: config.ssePort || 3001,
    });

    // Explicitly start the SSE transport
    logToFile('Starting SSE transport');
    await transport.start();
  } else {
    logToFile('Creating StdioServerTransport');
    transport = new StdioServerTransport();
  }

  // Connect the server to the transport
  logToFile('Connecting server to transport');
  server.connect(transport);

  logToFile('Transport connection established');

  // Log server status
  if (config.transport === 'sse') {
    logToFile(`Opik MCP Server running on SSE (port ${config.ssePort})`);
  } else {
    logToFile('Opik MCP Server running on stdio');
  }

  logToFile('Main function completed successfully');

  // Start heartbeat for keeping the process alive
  setInterval(() => {
    logToFile('Heartbeat ping');
  }, 5000);
}

// Start the server
main().catch(error => {
  logToFile(`Error starting server: ${error}`);
  process.exit(1);
});

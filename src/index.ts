import fs from 'fs';

// Import other modules
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

// Import custom transports
import { StreamableHttpTransport } from './transports/streamable-http-transport.js';

// Import environment variables loader - no console output
import './utils/env.js';
import { logToFile, logFile } from './utils/logging.js';

// Import tool loaders
import { loadTraceTools } from './tools/trace.js';
import { loadPromptTools } from './tools/prompt.js';
import { loadProjectTools } from './tools/project.js';
import { loadMetricTools } from './tools/metrics.js';
import { loadIntegrationTools } from './tools/integration.js';
import { loadCapabilitiesTools } from './tools/capabilities.js';
import { loadDatasetTools } from './tools/dataset.js';
import { registerResource } from './tools/registration.js';
import { callSdk, getOpikApi } from './utils/opik-sdk.js';
import { loadCorePrompts } from './prompts/core-prompts.js';

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
const enabledToolsets = new Set(config.enabledToolsets);

if (enabledToolsets.has('integration')) {
  server = loadIntegrationTools(server);
  logToFile('Loaded integration toolset');
}

if (enabledToolsets.has('core')) {
  server = loadCapabilitiesTools(server, config);
  logToFile('Loaded core capabilities tools');
  server = loadCorePrompts(server);
  logToFile('Loaded core prompts');

  server = loadProjectTools(server, { includeReadOps: true, includeMutations: false });
  logToFile('Loaded core project read tools');

  server = loadTraceTools(server, { includeCoreTools: true, includeExpertActions: false });
  logToFile('Loaded core trace tools');
}

if (enabledToolsets.has('expert-prompts')) {
  server = loadPromptTools(server);
  logToFile('Loaded expert prompts toolset');
}

if (enabledToolsets.has('expert-datasets')) {
  server = loadDatasetTools(server);
  logToFile('Loaded expert datasets toolset');
}

if (enabledToolsets.has('expert-project-actions')) {
  server = loadProjectTools(server, { includeReadOps: false, includeMutations: true });
  logToFile('Loaded expert project actions toolset');
}

if (enabledToolsets.has('expert-trace-actions')) {
  server = loadTraceTools(server, { includeCoreTools: false, includeExpertActions: true });
  logToFile('Loaded expert trace actions toolset');
}

if (enabledToolsets.has('metrics')) {
  server = loadMetricTools(server);
  logToFile('Loaded metrics toolset');
}

// Add resources to the MCP server
if (config.workspaceName) {
  // Define a workspace info resource
  registerResource(
    server,
    'workspace-info',
    'opik://workspace-info',
    'Workspace information for the configured Opik MCP server.',
    async () => ({
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
    })
  );

  // Define a projects resource that provides the list of projects in the workspace
  registerResource(
    server,
    'projects-list',
    'opik://projects-list',
    'Project listing for the configured Opik workspace.',
    async () => {
      try {
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.projects.findProjects());

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
    }
  );
}

// ----------- SERVER CONFIGURATION TOOLS -----------

// Main function to start the server
export async function main() {
  logToFile('Starting main function');

  // Create the appropriate transport based on configuration
  let transport;
  if (config.transport === 'streamable-http') {
    logToFile(`Creating Streamable HTTP transport on port ${config.streamableHttpPort}`);
    transport = new StreamableHttpTransport({
      port: config.streamableHttpPort || 3001,
      host: config.streamableHttpHost || '127.0.0.1',
    });

    // Explicitly start the remote transport host
    logToFile('Starting remote transport');
    await transport.start();
  } else {
    logToFile('Creating StdioServerTransport');
    transport = new StdioServerTransport();
  }

  // Connect the server to the transport
  logToFile('Connecting server to transport');
  await server.connect(transport);

  logToFile('Transport connection established');

  // Log server status
  if (config.transport === 'streamable-http') {
    logToFile(`Opik MCP Server running on Streamable HTTP (port ${config.streamableHttpPort})`);
  } else {
    logToFile('Opik MCP Server running on stdio');
  }

  logToFile('Main function completed successfully');
}

// Start the server
main().catch(error => {
  logToFile(`Error starting server: ${error}`);
  process.exit(1);
});

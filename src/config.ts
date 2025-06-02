/**
 * Configuration loader for Opik MCP server
 * Loads and validates environment variables from .env file
 * and/or command-line arguments
 */

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import * as fs from 'node:fs';

/**
 * File-based logger
 * Only writes if debug mode is enabled or being set to enabled
 */
function writeToLogFile(message: string, forceWrite: boolean = false): void {
  try {
    // Check if debug mode is enabled or being set to enabled
    // This check is special because we're in the process of parsing args
    const debugArg =
      process.argv.includes('--debug') &&
      process.argv[process.argv.indexOf('--debug') + 1] === 'true';
    const debugEnv = process.env.DEBUG_MODE === 'true';

    if (debugArg || debugEnv || forceWrite) {
      const logFile = '/tmp/opik-mcp.log';
      if (!fs.existsSync(logFile)) {
        fs.writeFileSync(logFile, `Opik MCP Server Started: ${new Date().toISOString()}\n`);
      }
      fs.appendFileSync(logFile, `[${new Date().toISOString()}] [config] ${message}\n`);
    }
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

interface OpikConfig {
  // API configuration
  apiBaseUrl: string;
  workspaceName?: string; // Optional for self-hosted version
  apiKey: string;
  isSelfHosted: boolean;
  debugMode: boolean;

  // Transport configuration
  transport: 'stdio' | 'sse';
  ssePort?: number;
  sseHost?: string;
  sseLogPath?: string;

  // MCP server configuration
  mcpName: string;
  mcpVersion: string;
  mcpPort?: number;
  mcpLogging: boolean;
  mcpDefaultWorkspace: string;
  mcpEnablePromptTools: boolean;
  mcpEnableProjectTools: boolean;
  mcpEnableTraceTools: boolean;
  mcpEnableMetricTools: boolean;
  mcpEnablePromptOptimizationTools: boolean;
}

/**
 * Parse command-line arguments
 */
function parseCommandLineArgs() {
  return (
    yargs(hideBin(process.argv))
      // API Configuration
      .option('apiUrl', {
        alias: 'url',
        type: 'string',
        description: 'API base URL',
      })
      .option('apiKey', {
        alias: 'key',
        type: 'string',
        description: 'API key for authentication',
      })
      .option('workspace', {
        alias: 'ws',
        type: 'string',
        description: 'Workspace name',
      })
      .option('selfHosted', {
        type: 'boolean',
        description: 'Whether the instance is self-hosted',
      })
      .option('debug', {
        type: 'boolean',
        description: 'Enable debug mode',
      })
      // Transport Configuration
      .option('transport', {
        type: 'string',
        description: 'Transport type (stdio or sse)',
        choices: ['stdio', 'sse'],
        default: 'stdio',
      })
      .option('ssePort', {
        type: 'number',
        description: 'Port for SSE transport',
        default: 3001,
      })
      .option('sseHost', {
        type: 'string',
        description: 'Host for SSE transport',
        default: 'localhost',
      })
      .option('sseLogPath', {
        type: 'string',
        description: 'Log file path for SSE transport',
        default: '/tmp/opik-mcp-sse.log',
      })
      // MCP Configuration
      .option('mcpName', {
        type: 'string',
        description: 'MCP server name',
      })
      .option('mcpVersion', {
        type: 'string',
        description: 'MCP server version',
      })
      .option('mcpPort', {
        type: 'number',
        description: 'MCP server port',
      })
      .option('mcpLogging', {
        type: 'boolean',
        description: 'Enable MCP server logging',
      })
      .option('mcpDefaultWorkspace', {
        type: 'string',
        description: 'Default workspace name',
      })
      // Tool enablement
      .option('disablePromptTools', {
        type: 'boolean',
        description: 'Disable prompt-related tools',
      })
      .option('disableProjectTools', {
        type: 'boolean',
        description: 'Disable project-related tools',
      })
      .option('disableTraceTools', {
        type: 'boolean',
        description: 'Disable trace-related tools',
      })
      .option('disableMetricTools', {
        type: 'boolean',
        description: 'Disable metric-related tools',
      })
      .option('disablePromptOptimizationTools', {
        type: 'boolean',
        description: 'Disable prompt optimization tools',
      })
      .help()
      .parse() as {
      apiUrl?: string;
      apiKey?: string;
      workspace?: string;
      selfHosted?: boolean;
      debug?: boolean;
      transport?: string;
      ssePort?: number;
      sseHost?: string;
      sseLogPath?: string;
      mcpName?: string;
      mcpVersion?: string;
      mcpPort?: number;
      mcpLogging?: boolean;
      mcpDefaultWorkspace?: string;
      disablePromptTools?: boolean;
      disableProjectTools?: boolean;
      disableTraceTools?: boolean;
      disableMetricTools?: boolean;
      disablePromptOptimizationTools?: boolean;
      [key: string]: unknown;
    }
  );
}

/**
 * Load environment variables with fallbacks
 */
export function loadConfig(): OpikConfig {
  // Parse command-line arguments first
  const args = parseCommandLineArgs();

  // Try to load from process.env and command-line args, with command-line taking precedence
  const config: OpikConfig = {
    // API configuration with fallbacks - with much more forgiving defaults
    apiBaseUrl: args.apiUrl || process.env.OPIK_API_BASE_URL || 'https://www.comet.com/opik/api',
    workspaceName: (args.workspace || process.env.OPIK_WORKSPACE_NAME || 'default').replace(
      /^['"](.*)['"]$/,
      '$1'
    ), // Remove any quotes
    apiKey: args.apiKey || process.env.OPIK_API_KEY || '',
    isSelfHosted:
      args.selfHosted !== undefined
        ? args.selfHosted
        : process.env.OPIK_SELF_HOSTED === 'true' || false,
    debugMode: args.debug !== undefined ? args.debug : process.env.DEBUG_MODE === 'true' || false,

    // Transport configuration
    transport: (args.transport || process.env.TRANSPORT || 'stdio') as 'stdio' | 'sse',
    ssePort: args.ssePort || (process.env.SSE_PORT ? parseInt(process.env.SSE_PORT, 10) : 3001),
    sseHost: args.sseHost || process.env.SSE_HOST || 'localhost',
    sseLogPath: args.sseLogPath || process.env.SSE_LOG_PATH || '/tmp/opik-mcp-sse.log',

    // MCP configuration with fallbacks
    mcpName: args.mcpName || process.env.MCP_NAME || 'opik-manager',
    mcpVersion: args.mcpVersion || process.env.MCP_VERSION || '1.0.0',
    mcpPort:
      args.mcpPort || (process.env.MCP_PORT ? parseInt(process.env.MCP_PORT, 10) : undefined),
    mcpLogging:
      args.mcpLogging !== undefined ? args.mcpLogging : process.env.MCP_LOGGING === 'true' || false,
    mcpDefaultWorkspace: args.mcpDefaultWorkspace || process.env.MCP_DEFAULT_WORKSPACE || 'default',

    // Tool enablement with fallbacks - note the logic reversal for the command-line args
    mcpEnablePromptTools: args.disablePromptTools
      ? false
      : process.env.MCP_ENABLE_PROMPT_TOOLS !== 'false', // Enable by default
    mcpEnableProjectTools: args.disableProjectTools
      ? false
      : process.env.MCP_ENABLE_PROJECT_TOOLS !== 'false', // Enable by default
    mcpEnableTraceTools: args.disableTraceTools
      ? false
      : process.env.MCP_ENABLE_TRACE_TOOLS !== 'false', // Enable by default
    mcpEnableMetricTools: args.disableMetricTools
      ? false
      : process.env.MCP_ENABLE_METRIC_TOOLS !== 'false', // Enable by default
    mcpEnablePromptOptimizationTools: args.disablePromptOptimizationTools
      ? false
      : process.env.MCP_ENABLE_PROMPT_OPTIMIZATION_TOOLS !== 'false', // Enable by default
  };

  // Validate required fields but be much more forgiving
  if (!config.apiKey) {
    // Only warn about missing API key, don't throw an error
    writeToLogFile(`Warning: No API key provided - some functionality will be limited`, true);
    // Still allow the server to start even without an API key
  }

  // Log configuration if in debug mode
  if (config.debugMode) {
    writeToLogFile('Opik MCP Configuration:');
    writeToLogFile(`- API Base URL: ${config.apiBaseUrl}`);
    writeToLogFile(`- Self-hosted: ${config.isSelfHosted ? 'Yes' : 'No'}`);
    if (!config.isSelfHosted) {
      writeToLogFile(`- Workspace: ${config.workspaceName}`);
    }
    writeToLogFile(`- Debug mode: ${config.debugMode ? 'Enabled' : 'Disabled'}`);

    // Log transport configuration
    writeToLogFile('\nTransport Configuration:');
    writeToLogFile(`- Transport: ${config.transport}`);
    if (config.transport === 'sse') {
      writeToLogFile(`- SSE Port: ${config.ssePort}`);
      writeToLogFile(`- SSE Host: ${config.sseHost}`);
      writeToLogFile(`- SSE Log Path: ${config.sseLogPath}`);
    }

    // Log MCP configuration
    writeToLogFile('\nMCP Configuration:');
    writeToLogFile(`- MCP Name: ${config.mcpName}`);
    writeToLogFile(`- MCP Version: ${config.mcpVersion}`);
    if (config.mcpPort) writeToLogFile(`- MCP Port: ${config.mcpPort}`);
    writeToLogFile(`- MCP Logging: ${config.mcpLogging ? 'Enabled' : 'Disabled'}`);
    writeToLogFile(`- MCP Default Workspace: ${config.mcpDefaultWorkspace}`);
    writeToLogFile(`- Prompt Tools: ${config.mcpEnablePromptTools ? 'Enabled' : 'Disabled'}`);
    writeToLogFile(`- Project Tools: ${config.mcpEnableProjectTools ? 'Enabled' : 'Disabled'}`);
    writeToLogFile(`- Trace Tools: ${config.mcpEnableTraceTools ? 'Enabled' : 'Disabled'}`);
    writeToLogFile(`- Metric Tools: ${config.mcpEnableMetricTools ? 'Enabled' : 'Disabled'}`);
  }

  return config;
}

// Export the configuration
const config = loadConfig();
export default config;

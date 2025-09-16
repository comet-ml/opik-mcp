/**
 * Configuration loader for Opik MCP server
 * Loads and validates environment variables from .env file
 * and/or command-line arguments
 */

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

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

// Available toolsets
export type OpikToolset =
  | 'capabilities' // Server info and help tools
  | 'integration' // Integration documentation and guides
  | 'prompts' // Prompt management tools
  | 'projects' // Project/workspace management tools
  | 'traces' // Trace listing and analysis tools
  | 'metrics'; // Metrics and analytics tools

export const DEFAULT_TOOLSETS: OpikToolset[] = ['integration', 'prompts', 'projects', 'traces'];

interface OpikFileConfig {
  api_key?: string;
  workspace?: string;
  url_override?: string;
}

/**
 * Load configuration from ~/.opik.config file
 */
function loadOpikConfigFile(): OpikFileConfig {
  try {
    const configPath = path.join(os.homedir(), '.opik.config');

    if (!fs.existsSync(configPath)) {
      return {};
    }

    const configContent = fs.readFileSync(configPath, 'utf8');
    const config: OpikFileConfig = {};

    // Parse INI-style format
    const lines = configContent.split('\n');
    let inOpikSection = false;

    for (const line of lines) {
      const trimmedLine = line.trim();

      // Skip empty lines and comments
      if (!trimmedLine || trimmedLine.startsWith('#') || trimmedLine.startsWith(';')) {
        continue;
      }

      // Check for section headers
      if (trimmedLine.startsWith('[') && trimmedLine.endsWith(']')) {
        inOpikSection = trimmedLine === '[opik]';
        continue;
      }

      // Only parse lines in the [opik] section
      if (!inOpikSection) {
        continue;
      }

      // Parse key = value pairs
      const equalIndex = trimmedLine.indexOf('=');
      if (equalIndex > 0) {
        const key = trimmedLine.substring(0, equalIndex).trim();
        const value = trimmedLine.substring(equalIndex + 1).trim();

        // Map the config keys to our expected format
        if (key === 'api_key') {
          config.api_key = value;
        } else if (key === 'workspace') {
          config.workspace = value;
        } else if (key === 'url_override') {
          config.url_override = value;
        }
      }
    }

    writeToLogFile(`Loaded config from ~/.opik.config: ${JSON.stringify(config)}`);
    return config;
  } catch (error) {
    writeToLogFile(`Failed to load ~/.opik.config: ${error}`);
    return {};
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

  // Toolset configuration - replaces individual tool flags
  enabledToolsets: OpikToolset[];
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
      // Toolset configuration
      .option('toolsets', {
        type: 'array',
        description: 'Comma-separated list of toolsets to enable',
        choices: ['capabilities', 'integration', 'prompts', 'projects', 'traces', 'metrics'],
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
      toolsets?: string[];
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

  // Load config from ~/.opik.config file
  const opikFileConfig = loadOpikConfigFile();

  // Try to load from process.env and command-line args, with command-line taking precedence
  const config: OpikConfig = {
    // API configuration with fallbacks - with much more forgiving defaults
    apiBaseUrl: args.apiUrl || process.env.OPIK_API_BASE_URL || opikFileConfig.url_override || 'https://www.comet.com/opik/api',
    workspaceName: (args.workspace || process.env.OPIK_WORKSPACE_NAME || opikFileConfig.workspace || 'default').replace(
      /^['"](.*)['"]$/,
      '$1'
    ), // Remove any quotes
    apiKey: args.apiKey || process.env.OPIK_API_KEY || opikFileConfig.api_key || '',
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

    // Toolset configuration with fallbacks
    enabledToolsets: (() => {
      // Command line takes precedence
      if (args.toolsets && args.toolsets.length > 0) {
        return args.toolsets.filter((t): t is OpikToolset =>
          ['integration', 'prompts', 'projects', 'traces', 'metrics'].includes(t)
        );
      }

      // Environment variable fallback
      if (process.env.OPIK_TOOLSETS) {
        const envToolsets = process.env.OPIK_TOOLSETS.split(',').map(t => t.trim());
        return envToolsets.filter((t): t is OpikToolset =>
          ['integration', 'prompts', 'projects', 'traces', 'metrics'].includes(t)
        );
      }

      // Default toolsets
      return DEFAULT_TOOLSETS;
    })(),
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
    writeToLogFile(`- API Key: ${config.apiKey ? '[REDACTED]' : '[NOT SET]'}`);
    writeToLogFile(`- Debug mode: ${config.debugMode ? 'Enabled' : 'Disabled'}`);

    // Log config sources
    writeToLogFile('\nConfiguration Sources:');
    if (Object.keys(opikFileConfig).length > 0) {
      writeToLogFile(`- Found ~/.opik.config with keys: ${Object.keys(opikFileConfig).join(', ')}`);
    } else {
      writeToLogFile('- No ~/.opik.config file found');
    }

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
    writeToLogFile(`- Enabled Toolsets: ${config.enabledToolsets.join(', ')}`);
  }

  return config;
}

// Export the configuration
const config = loadConfig();
export default config;

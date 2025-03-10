/**
 * Configuration loader for Opik MCP server
 * Loads and validates environment variables from .env file
 * and/or command-line arguments
 */

import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';

interface OpikConfig {
  // API configuration
  apiBaseUrl: string;
  workspaceName?: string; // Optional for self-hosted version
  apiKey: string;
  isSelfHosted: boolean;
  debugMode: boolean;

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
}

/**
 * Parse command-line arguments
 */
function parseCommandLineArgs() {
  return yargs(hideBin(process.argv))
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
    .help()
    .parse() as {
      apiUrl?: string;
      apiKey?: string;
      workspace?: string;
      selfHosted?: boolean;
      debug?: boolean;
      mcpName?: string;
      mcpVersion?: string;
      mcpPort?: number;
      mcpLogging?: boolean;
      mcpDefaultWorkspace?: string;
      disablePromptTools?: boolean;
      disableProjectTools?: boolean;
      disableTraceTools?: boolean;
      disableMetricTools?: boolean;
      [key: string]: unknown;
    };
}

/**
 * Load environment variables with fallbacks
 */
function loadConfig(): OpikConfig {
  // Parse command-line arguments first
  const args = parseCommandLineArgs();

  // Try to load from process.env and command-line args, with command-line taking precedence
  const config: OpikConfig = {
    // API configuration with fallbacks
    apiBaseUrl: args.apiUrl || process.env.OPIK_API_BASE_URL || "",
    workspaceName: args.workspace || process.env.OPIK_WORKSPACE_NAME || "",
    apiKey: args.apiKey || process.env.OPIK_API_KEY || "",
    isSelfHosted: args.selfHosted !== undefined ? args.selfHosted :
                  process.env.OPIK_SELF_HOSTED === "true" || false,
    debugMode: args.debug !== undefined ? args.debug :
               process.env.DEBUG_MODE === "true" || false,

    // MCP configuration with fallbacks
    mcpName: args.mcpName || process.env.MCP_NAME || "opik-manager",
    mcpVersion: args.mcpVersion || process.env.MCP_VERSION || "1.0.0",
    mcpPort: args.mcpPort || (process.env.MCP_PORT ? parseInt(process.env.MCP_PORT, 10) : undefined),
    mcpLogging: args.mcpLogging !== undefined ? args.mcpLogging :
                process.env.MCP_LOGGING === "true" || false,
    mcpDefaultWorkspace: args.mcpDefaultWorkspace || process.env.MCP_DEFAULT_WORKSPACE || "default",

    // Tool enablement with fallbacks - note the logic reversal for the command-line args
    mcpEnablePromptTools: args.disablePromptTools ? false :
                         process.env.MCP_ENABLE_PROMPT_TOOLS !== "false", // Enable by default
    mcpEnableProjectTools: args.disableProjectTools ? false :
                          process.env.MCP_ENABLE_PROJECT_TOOLS !== "false", // Enable by default
    mcpEnableTraceTools: args.disableTraceTools ? false :
                        process.env.MCP_ENABLE_TRACE_TOOLS !== "false", // Enable by default
    mcpEnableMetricTools: args.disableMetricTools ? false :
                         process.env.MCP_ENABLE_METRIC_TOOLS !== "false" // Enable by default
  };

  // Validate required fields
  const missingFields: string[] = [];

  if (!config.apiBaseUrl) missingFields.push("apiBaseUrl (--apiUrl or OPIK_API_BASE_URL)");
  if (!config.apiKey) missingFields.push("apiKey (--apiKey or OPIK_API_KEY)");
  if (!config.isSelfHosted && !config.workspaceName) missingFields.push("workspaceName (--workspace or OPIK_WORKSPACE_NAME, required for cloud deployment)");

  if (missingFields.length > 0) {
    const errorMessage = `Missing required configuration: ${missingFields.join(", ")}`;
    console.error(`Configuration Error: ${errorMessage}`);
    console.error("Please provide configuration via environment variables or command-line arguments");
    throw new Error(errorMessage);
  }

  // If workspaceName isn't provided, use the default
  if (!config.workspaceName && !config.isSelfHosted) {
    config.workspaceName = config.mcpDefaultWorkspace;
    if (config.debugMode) {
      console.log(`No workspace provided, using default: ${config.mcpDefaultWorkspace}`);
    }
  }

  // Log configuration if in debug mode
  if (config.debugMode) {
    console.log("Opik MCP Configuration:");
    console.log(`- API Base URL: ${config.apiBaseUrl}`);
    console.log(`- Self-hosted: ${config.isSelfHosted ? "Yes" : "No"}`);
    if (!config.isSelfHosted) {
      console.log(`- Workspace: ${config.workspaceName}`);
    }
    console.log(`- Debug mode: ${config.debugMode ? "Enabled" : "Disabled"}`);

    // Log MCP configuration
    console.log("\nMCP Configuration:");
    console.log(`- MCP Name: ${config.mcpName}`);
    console.log(`- MCP Version: ${config.mcpVersion}`);
    if (config.mcpPort) console.log(`- MCP Port: ${config.mcpPort}`);
    console.log(`- MCP Logging: ${config.mcpLogging ? "Enabled" : "Disabled"}`);
    console.log(`- MCP Default Workspace: ${config.mcpDefaultWorkspace}`);
    console.log(`- Prompt Tools: ${config.mcpEnablePromptTools ? "Enabled" : "Disabled"}`);
    console.log(`- Project Tools: ${config.mcpEnableProjectTools ? "Enabled" : "Disabled"}`);
    console.log(`- Trace Tools: ${config.mcpEnableTraceTools ? "Enabled" : "Disabled"}`);
    console.log(`- Metric Tools: ${config.mcpEnableMetricTools ? "Enabled" : "Disabled"}`);
  }

  return config;
}

// Export the configuration
const config = loadConfig();
export default config;

/**
 * Configuration loader for Opik MCP server
 * Loads and validates environment variables from .env file
 */

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
 * Load environment variables with fallbacks
 */
function loadConfig(): OpikConfig {
  // Try to load from process.env
  const config: OpikConfig = {
    // API configuration with fallbacks
    apiBaseUrl: process.env.OPIK_API_BASE_URL || "",
    workspaceName: process.env.OPIK_WORKSPACE_NAME || "",
    apiKey: process.env.OPIK_API_KEY || "",
    isSelfHosted: process.env.OPIK_SELF_HOSTED === "true" || false,
    debugMode: process.env.DEBUG_MODE === "true" || false,

    // MCP configuration with fallbacks
    mcpName: process.env.MCP_NAME || "opik-manager",
    mcpVersion: process.env.MCP_VERSION || "1.0.0",
    mcpPort: process.env.MCP_PORT ? parseInt(process.env.MCP_PORT, 10) : undefined,
    mcpLogging: process.env.MCP_LOGGING === "true" || false,
    mcpDefaultWorkspace: process.env.MCP_DEFAULT_WORKSPACE || "default",
    mcpEnablePromptTools: process.env.MCP_ENABLE_PROMPT_TOOLS !== "false", // Enable by default
    mcpEnableProjectTools: process.env.MCP_ENABLE_PROJECT_TOOLS !== "false", // Enable by default
    mcpEnableTraceTools: process.env.MCP_ENABLE_TRACE_TOOLS !== "false", // Enable by default
    mcpEnableMetricTools: process.env.MCP_ENABLE_METRIC_TOOLS !== "false" // Enable by default
  };

  // Validate required fields
  const missingFields: string[] = [];

  if (!config.apiBaseUrl) missingFields.push("OPIK_API_BASE_URL");
  if (!config.apiKey) missingFields.push("OPIK_API_KEY");
  if (!config.isSelfHosted && !config.workspaceName) missingFields.push("OPIK_WORKSPACE_NAME (required for cloud deployment)");

  if (missingFields.length > 0) {
    const errorMessage = `Missing required environment variables: ${missingFields.join(", ")}`;
    console.error(`Configuration Error: ${errorMessage}`);
    console.error("Please ensure you have created a .env file based on .env.example");
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

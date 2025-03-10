/**
 * Configuration loader for Opik MCP server
 * Loads and validates environment variables from .env file
 */

interface OpikConfig {
  apiBaseUrl: string;
  workspaceName?: string; // Optional for self-hosted version
  apiKey: string;
  isSelfHosted: boolean;
  debugMode: boolean;
}

/**
 * Load environment variables with fallbacks
 */
function loadConfig(): OpikConfig {
  // Try to load from process.env
  const config: OpikConfig = {
    apiBaseUrl: process.env.OPIK_API_BASE_URL || "",
    workspaceName: process.env.OPIK_WORKSPACE_NAME || "",
    apiKey: process.env.OPIK_API_KEY || "",
    isSelfHosted: process.env.OPIK_SELF_HOSTED === "true" || false,
    debugMode: process.env.DEBUG_MODE === "true" || false
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

  // Log configuration if in debug mode
  if (config.debugMode) {
    console.log("Opik MCP Configuration:");
    console.log(`- API Base URL: ${config.apiBaseUrl}`);
    console.log(`- Self-hosted: ${config.isSelfHosted ? "Yes" : "No"}`);
    if (!config.isSelfHosted) {
      console.log(`- Workspace: ${config.workspaceName}`);
    }
  }

  return config;
}

// Export the configuration
const config = loadConfig();
export default config;

/**
 * Environment loading utility
 * Loads variables from .env file
 */
import { config as dotenvConfig } from 'dotenv';
import * as fs from 'node:fs';
import * as path from 'node:path';

/**
 * File-based logger
 * Only writes if debug mode is enabled
 */
function writeToLogFile(message: string): void {
  try {
    // This uses sync functions which is acceptable during initialization
    // The log file will only be written to if DEBUG_MODE=true
    if (process.env.DEBUG_MODE === 'true') {
      const logFile = '/tmp/opik-mcp.log';
      if (!fs.existsSync(logFile)) {
        fs.writeFileSync(logFile, `Opik MCP Server Started: ${new Date().toISOString()}\n`);
      }
      fs.appendFileSync(logFile, `[${new Date().toISOString()}] [env] ${message}\n`);
    }
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

/**
 * Attempts to load environment variables from .env file
 * Falls back to .env.example if .env doesn't exist
 */
export function loadEnv(): void {
  const envPath = path.resolve(process.cwd(), '.env');
  const examplePath = path.resolve(process.cwd(), '.env.example');

  // Check if .env exists
  if (fs.existsSync(envPath)) {
    // Log this to file instead of console
    writeToLogFile('Loading environment from .env file');
    dotenvConfig({ path: envPath });
  } else if (fs.existsSync(examplePath)) {
    // Fall back to .env.example if .env doesn't exist
    writeToLogFile('Warning: .env file not found, using .env.example as fallback');
    writeToLogFile('Please create a .env file with your actual configuration');
    dotenvConfig({ path: examplePath });
  } else {
    writeToLogFile('Warning: No .env or .env.example file found');
  }
}

// Load environment variables when this module is imported
loadEnv();

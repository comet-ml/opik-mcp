import fs from 'fs';
import { loadConfig } from './../config.js';

const config = loadConfig();
export const logFile = '/tmp/opik-mcp.log';

export function logToFile(message: string) {
  // Only log if debug mode is enabled
  if (!config?.debugMode) return;

  try {
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logFile, `[${timestamp}] ${message}\n`);
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

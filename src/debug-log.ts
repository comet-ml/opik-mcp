/**
 * Debug logging utility for MCP server
 */

// Write directly to a file for debugging
import fs from 'fs';

export function initDebugLog() {
  const logPath = '/tmp/opik-mcp-debug.log';

  // Clear previous log
  try {
    fs.writeFileSync(logPath, 'MCP DEBUG LOG STARTED\n');
  } catch (error) {
    // If we can't write to the file, use console.error
    console.error('Failed to write to debug log file:', error);
  }

  // Log startup information
  logDebug('MCP Server starting initialization');
  logDebug(`Process ID: ${process.pid}`);
  logDebug(`Node Version: ${process.version}`);
  logDebug(`Working Directory: ${process.cwd()}`);

  // Log environment variables
  logDebug('Environment Variables:');
  Object.keys(process.env)
    .filter(key => key.startsWith('OPIK_'))
    .forEach(key => {
      let value = process.env[key];
      // Mask sensitive information
      if (key === 'OPIK_API_KEY') value = '***MASKED***';
      logDebug(`  ${key}: ${value}`);
    });

  // Log command line arguments
  logDebug('Command Line Arguments:');
  process.argv.forEach((arg, index) => {
    logDebug(`  ${index}: ${arg}`);
  });

  // Set up uncaught exception handler
  process.on('uncaughtException', (error) => {
    logDebug(`UNCAUGHT EXCEPTION: ${error.message}`);
    logDebug(error.stack || 'No stack trace available');
  });

  // Set up unhandled rejection handler
  process.on('unhandledRejection', (reason, promise) => {
    logDebug(`UNHANDLED REJECTION: ${reason}`);
  });

  // Set up exit handler
  process.on('exit', (code) => {
    logDebug(`Process exiting with code: ${code}`);
  });
}

export function logDebug(message: string) {
  const logPath = '/tmp/opik-mcp-debug.log';
  const timestamp = new Date().toISOString();
  const logMessage = `[${timestamp}] ${message}\n`;

  try {
    fs.appendFileSync(logPath, logMessage);
  } catch (error) {
    console.error('Failed to append to debug log file:', error);
  }
}

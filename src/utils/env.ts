/**
 * Environment loading utility
 * Loads variables from .env file
 */
import { config as dotenvConfig } from 'dotenv';
import * as fs from 'node:fs';
import * as path from 'node:path';

/**
 * Attempts to load environment variables from .env file
 * Falls back to .env.example if .env doesn't exist
 */
export function loadEnv(): void {
  const envPath = path.resolve(process.cwd(), '.env');
  const examplePath = path.resolve(process.cwd(), '.env.example');

  // Check if .env exists
  if (fs.existsSync(envPath)) {
    console.log('Loading environment from .env file');
    dotenvConfig({ path: envPath });
  } else if (fs.existsSync(examplePath)) {
    // Fall back to .env.example if .env doesn't exist
    console.warn('Warning: .env file not found, using .env.example as fallback');
    console.warn('Please create a .env file with your actual configuration');
    dotenvConfig({ path: examplePath });
  } else {
    console.warn('Warning: No .env or .env.example file found');
  }
}

// Load environment variables when this module is imported
loadEnv();

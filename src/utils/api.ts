import fetch, { RequestInit } from 'node-fetch';
import { loadConfig } from './../config.js';
import { logToFile } from './logging.js';

const config = loadConfig();

export const makeApiRequest = async <T>(
  path: string,
  options: RequestInit = {},
  workspaceName?: string
): Promise<{ data: T | null; error: string | null }> => {
  // Prepare headers based on configuration
  const API_HEADERS: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    authorization: config.apiKey,
  };

  // Add workspace header for cloud version
  if (!config.isSelfHosted) {
    // Use provided workspace name or fall back to config
    const wsName = workspaceName || config.workspaceName;

    if (wsName) {
      // Note: The Opik API expects the workspace name to be the default workspace.
      // Project names like "Therapist Chat" are not valid workspace names.
      // The API will return a 400 error if a non-existent workspace is specified.
      const workspaceNameToUse = wsName.trim();
      logToFile(
        `DEBUG - Workspace name before setting header: "${workspaceNameToUse}", type: ${typeof workspaceNameToUse}, length: ${workspaceNameToUse.length}`
      );

      // Use the raw workspace name - do not encode it
      API_HEADERS['Comet-Workspace'] = workspaceNameToUse;
      logToFile(`Using workspace: ${workspaceNameToUse}`);
    }
  }

  const url = `${config.apiBaseUrl}${path}`;
  logToFile(`Making API request to: ${url}`);
  logToFile(`Headers: ${JSON.stringify(API_HEADERS, null, 2)}`);

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...API_HEADERS,
        ...options.headers,
      },
    });

    // Get response body text for better error handling
    const responseText = await response.text();
    let responseData: any = null;

    // Try to parse the response as JSON
    try {
      responseData = JSON.parse(responseText);
    } catch (e) {
      // If it's not valid JSON, use the raw text
      responseData = responseText;
    }

    if (!response.ok) {
      const errorMsg = `HTTP error! status: ${response.status} ${JSON.stringify(responseData)}`;
      logToFile(`API Error: ${errorMsg}`);
      return {
        data: null,
        error: errorMsg,
      };
    }

    return {
      data: responseData as T,
      error: null,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    logToFile(`Error making API request: ${errorMessage}`);
    return {
      data: null,
      error: errorMessage,
    };
  }
};

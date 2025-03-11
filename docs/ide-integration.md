# IDE Integration

This document explains how to integrate the Opik MCP server with Cursor IDE.

## Cursor IDE Integration

To use the Opik MCP server with Cursor IDE, you need to create a `.cursor/mcp.json` file in your project root. This file tells Cursor how to start and configure the MCP server.

### Basic Configuration

Here's a basic configuration to get started:

```json
{
  "mcpServers": {
    "opik": {
      "command": "node",
      "args": [
        "/path/to/build/index.js",
        "--apiUrl", "https://www.comet.com/opik/api",
        "--apiKey", "your-api-key",
        "--workspace", "default"
      ]
    }
  }
}
```

### Comprehensive Configuration

Here's a comprehensive example with all available configuration options:

```json
{
  "mcpServers": {
    "opik": {
      "command": "node",
      "args": [
        "/absolute/path/to/build/index.js",

        // API Configuration
        "--apiUrl", "https://www.comet.com/opik/api",
        "--apiKey", "your-api-key",
        "--workspace", "default",  // Use "default" unless you have created additional workspaces

        // Deployment Configuration
        "--selfHosted", "false",

        // Debug Settings
        "--debug", "false",

        // MCP Server Configuration
        "--mcpName", "opik-manager",
        "--mcpVersion", "1.0.0",
        "--mcpLogging", "false",
        "--mcpDefaultWorkspace", "default",

        // Tool Enablement (omit these to use defaults)
        "--disablePromptTools", "false",
        "--disableProjectTools", "false",
        "--disableTraceTools", "false",
        "--disableMetricTools", "false"
      ],
      "env": {
        // You can also set environment variables here if preferred
        // "OPIK_API_KEY": "your-api-key"
      }
    }
  }
}
```

### With Environment Variables

You can also use environment variables instead of command-line arguments:

```json
{
  "mcpServers": {
    "opik": {
      "command": "node",
      "args": [
        "/path/to/build/index.js"
      ],
      "env": {
        "OPIK_API_BASE_URL": "https://www.comet.com/opik/api",
        "OPIK_API_KEY": "your-api-key",
        "OPIK_WORKSPACE_NAME": "default",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

### Using SSE Transport

If you want to use the SSE transport:

```json
{
  "mcpServers": {
    "opik": {
      "command": "node",
      "args": [
        "/path/to/build/cli.js",
        "serve",
        "--transport", "sse",
        "--port", "3001"
      ],
      "env": {
        "OPIK_API_BASE_URL": "https://www.comet.com/opik/api",
        "OPIK_API_KEY": "your-api-key",
        "OPIK_WORKSPACE_NAME": "default"
      }
    }
  }
}
```

## Important Notes

1. **Absolute Path**: Make sure to use an absolute path to the `index.js` file to ensure Cursor can find it regardless of the working directory.

2. **Workspaces vs Projects**: Do not confuse workspaces with projects:
   - The workspace name is typically "default" for most users
   - Project names (like "Therapist Chat") are NOT valid workspace names
   - Using a project name as a workspace name will result in a 400 error from the API

3. **Working with Specific Projects**: When using the MCP tools, you can specify which project to work with by:
   - Using the `projectId` parameter with the project's unique identifier
   - Using the `projectName` parameter with the project's human-readable name
   - If neither is specified, the server will use the first available project

4. **Environment Variables with Spaces**: If your environment variables contain spaces, you need to be careful with quoting:

```json
{
  "mcpServers": {
    "opik": {
      "command": "node",
      "args": [
        "/path/to/build/index.js"
      ],
      "env": {
        "OPIK_WORKSPACE_NAME": "Workspace With Spaces"
      }
    }
  }
}
```

## Enabling the MCP in Cursor

After creating the configuration file:

1. Open Cursor IDE
2. Navigate to Settings > MCP
3. Enable the Opik MCP in your Cursor settings
4. Restart Cursor IDE if necessary

## Troubleshooting

If you encounter issues with the MCP connection:

1. Check the Cursor console logs for errors (Help > Toggle Developer Tools)
2. Verify your API key and other configuration settings
3. Ensure the server can be accessed from Cursor (if using SSE transport)
4. Check for firewall or network issues (if using SSE transport)

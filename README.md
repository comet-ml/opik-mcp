# Test Opik MCP

This repository contains a Model Context Protocol (MCP) implementation for managing prompts. It provides a set of tools for creating, updating, listing, and managing prompts through a standardized interface.

## Features

- List all prompts with pagination
- Create new prompts
- Create new versions of existing prompts
- Get prompt details by ID
- Update prompt information
- Delete prompts

## Installation

To use this MCP implementation, follow these steps:

1. Clone this repository:

   ```bash
   git clone https://github.com/your-username/prompt-manager-mcp.git
   cd prompt-manager-mcp
   ```

2. Create a `.cursor/msp.json` file in your project root:

   ```bash
   mkdir -p .cursor
   touch .cursor/msp.json
   ```

3. Add the following configuration to the `.cursor/msp.json` file:

   ```json
   {
     "mcpServers": {
       "opik": {
         "command": "node",
         "args": ["/Users/{username}/{path}/test-opik-mcp/build/index.js"]
       }
     }
   }
   ```

4. Update the `src/index.ts` file with your API configuration:

   ```typescript
   const API_BASE_URL = "https://comet.com/opik/api"; // Replace with your API URL
   const WORKSPACE_NAME = "your-workspace-name"; // Replace with your workspace name
   const API_KEY = "your-api-key"; // Replace with your API key
   ```

5. Install dependencies and build the project:

   ```bash
   npm install
   npm run build
   ```

6. Open Cursor and navigate to Settings > MCP

7. Enable the Opik MCP in your Cursor settings

## Configuration

The MCP server requires the following environment variables:

- `API_BASE_URL`: The base URL for the API (exmaple: "https://comet.com/opik/api")
- `API_KEY`: Your API key for authentication
- `WORKSPACE_NAME`: Your workspace name

## Available Tools

### 1. List Prompts

Lists all available prompts with pagination support.

```typescript
{
  name: "list-prompts",
  parameters: {
    page: number,    // Page number for pagination
    size: number     // Number of items per page
  }
}
```

### 2. Create Prompt

Creates a new prompt.

```typescript
{
  name: "create-prompt",
  parameters: {
    name: string     // Name of the prompt
  }
}
```

### 3. Create Prompt Version

Creates a new version of an existing prompt.

```typescript
{
  name: "create-prompt-version",
  parameters: {
    name: string,           // Name of the original prompt
    template: string,       // Template content for the prompt version
    commit_message: string  // Commit message for the prompt version
  }
}
```

### 4. Get Prompt by ID

Retrieves details of a specific prompt.

```typescript
{
  name: "get-prompt-by-id",
  parameters: {
    promptId: string  // ID of the prompt to fetch
  }
}
```

### 5. Update Prompt

Updates an existing prompt's information.

```typescript
{
  name: "update-prompt",
  parameters: {
    promptId: string,  // ID of the prompt to update
    name: string       // New name for the prompt
  }
}
```

### 6. Delete Prompt

Deletes an existing prompt.

```typescript
{
  name: "delete-prompt",
  parameters: {
    promptId: string  // ID of the prompt to delete
  }
}
```

## Response Format

All tools return responses in the following format:

```typescript
{
  content: [
    {
      type: "text",
      text: string, // Response message or error
    },
  ];
}
```

## Security

- API keys are required for authentication
- All requests are made over HTTPS
- Sensitive information is handled securely

## Contributing

Feel free to submit issues and enhancement requests!

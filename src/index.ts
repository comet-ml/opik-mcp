import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

// Check for API key
const API_BASE_URL = "YOUR VALUE HERE";
const WORKSPACE_NAME = "YOUR VALUE HERE";
const API_KEY = "YOUR VALUE HERE";

if (!API_BASE_URL || !WORKSPACE_NAME || !API_KEY) {
  console.error(
    "Error: API_BASE_URL, WORKSPACE_NAME and API_KEY environment variables are required"
  );
  process.exit(1);
}

// Types
type PromptResponse = {
  page: number;
  size: number;
  total: number;
  content: Array<{
    name: string;
    id: string;
    description: string;
    created_at: string;
    created_by: string;
    last_updated_at: string;
    last_updated_by: string;
    version_count: number;
  }>;
};

type SinglePromptResponse = Omit<PromptResponse["content"][0], never>;

// Helper function to make requests to API
const makeApiRequest = async <T>(
  path: string,
  options: RequestInit = {}
): Promise<{ data: T | null; error: string | null }> => {
  const API_HEADERS = {
    Accept: "application/json",
    "Comet-Workspace": WORKSPACE_NAME as string,
    "Content-Type": "application/json",
    authorization: API_KEY as string,
  };

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        ...API_HEADERS,
        ...options.headers,
      },
    });

    if (!response.ok) {
      return {
        data: null,
        error: `HTTP error! status: ${response.status} ${JSON.stringify(
          response.body
        )}`,
      };
    }

    const data = (await response.json()) as T;
    return {
      data,
      error: null,
    };
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error occurred";
    console.error("Error making API request:", error);
    return {
      data: null,
      error: errorMessage,
    };
  }
};

// Create and configure server
const server = new McpServer({
  name: "prompt-manager",
  version: "1.0.0",
});

server.tool(
  "list-prompts",
  "Get a list of Opik prompts",
  {
    page: z.number().describe("Page number for pagination"),
    size: z.number().describe("Number of items per page"),
  },
  async (args) => {
    const response = await makeApiRequest<PromptResponse>(
      `/v1/private/prompts?page=${args.page}&size=${args.size}`
    );

    if (!response.data) {
      return {
        content: [
          { type: "text", text: response.error || "Failed to fetch prompts" },
        ],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: `Found ${response.data.total} prompts (showing page ${
            response.data.page
          } of ${Math.ceil(response.data.total / response.data.size)})`,
        },
        {
          type: "text",
          text: JSON.stringify(response.data.content, null, 2),
        },
      ],
    };
  }
);

server.tool(
  "create-prompt",
  "Create a new prompt",
  {
    name: z.string().describe("Name of the prompt"),
  },
  async (args) => {
    const { name } = args;
    const response = await makeApiRequest<void>(`/v1/private/prompts`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });

    return {
      content: [
        {
          type: "text",
          text: response.error || "Successfully created prompt",
        },
      ],
    };
  }
);

server.tool(
  "create-prompt-version",
  "Create a new version of a prompt",
  {
    name: z.string().describe("Name of the original prompt"),
    template: z.string().describe("Template content for the prompt version"),
    commit_message: z
      .string()
      .describe("Commit message for the prompt version"),
  },
  async (args) => {
    const { name, template, commit_message } = args;
    const response = await makeApiRequest<any>(`/v1/private/prompts/versions`, {
      method: "POST",
      body: JSON.stringify({
        name,
        version: { template, change_description: commit_message },
      }),
    });

    return {
      content: [
        {
          type: "text",
          text: response.data
            ? "Successfully created prompt version"
            : `${response.error} ${JSON.stringify(args)}` ||
              "Failed to create prompt version",
        },
      ],
    };
  }
);

server.tool(
  "get-prompt-by-id",
  "Get a single prompt by ID",
  {
    promptId: z.string().describe("ID of the prompt to fetch"),
  },
  async (args) => {
    const { promptId } = args;
    const response = await makeApiRequest<SinglePromptResponse>(
      `/v1/private/prompts/${promptId}`
    );

    if (!response.data) {
      return {
        content: [
          { type: "text", text: response.error || "Failed to fetch prompt" },
        ],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(response.data, null, 2),
        },
      ],
    };
  }
);

server.tool(
  "update-prompt",
  "Update a prompt",
  {
    promptId: z.string().describe("ID of the prompt to update"),
    name: z.string().describe("New name for the prompt"),
  },
  async (args) => {
    const { promptId, name } = args;
    const response = await makeApiRequest<void>(
      `/v1/private/prompts/${promptId}`,
      {
        method: "PUT",
        body: JSON.stringify({ name }),
        headers: {
          "Content-Type": "application/json",
        },
      }
    );

    return {
      content: [
        {
          type: "text",
          text: !response.error
            ? "Successfully updated prompt"
            : response.error || "Failed to update prompt",
        },
      ],
    };
  }
);

server.tool(
  "delete-prompt",
  "Delete a prompt",
  {
    promptId: z.string().describe("ID of the prompt to delete"),
  },
  async (args) => {
    const { promptId } = args;
    const response = await makeApiRequest<void>(
      `/v1/private/prompts/${promptId}`,
      {
        method: "DELETE",
      }
    );

    return {
      content: [
        {
          type: "text",
          text: !response.error
            ? "Successfully deleted prompt"
            : response.error || "Failed to delete prompt",
        },
      ],
    };
  }
);

// Server startup
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Prompt Manager MCP Server running on stdio");
}

main().catch((error) => {
  console.error("Fatal error in main():", error);
  process.exit(1);
});

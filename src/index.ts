import fs from 'fs';

// Import other modules
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

// Import environment variables loader - no console output
import './utils/env.js';

// Setup file-based logging
const logFile = '/tmp/opik-mcp.log';

// Import configuration
import configImport from './config.js';
const config = configImport;

// Clean stdout protocol message - only use this for protocol communication
function sendProtocolMessage(method: string, message: string) {
  console.log(JSON.stringify({
    jsonrpc: "2.0",
    method,
    params: { message }
  }));
}

// Define logging functions
function logToFile(message: string) {
  // Only log if debug mode is enabled
  if (!config?.debugMode) return;

  try {
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logFile, `[${timestamp}] ${message}\n`);
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

// Only initialize log file if debug mode is enabled
if (config.debugMode) {
  try {
    fs.writeFileSync(logFile, `Opik MCP Server Started: ${new Date().toISOString()}\n`);

    // Log process info
    logToFile(`Process ID: ${process.pid}, Node Version: ${process.version}`);
    logToFile(`Arguments: ${process.argv.join(' ')}`);
    logToFile(`Loaded configuration: API=${config.apiBaseUrl}, Workspace=${config.workspaceName || 'None'}`);

    // Register error handlers
    process.on('uncaughtException', (err) => {
      logToFile(`UNCAUGHT EXCEPTION: ${err.message}`);
      logToFile(err.stack || 'No stack trace');
    });

    process.on('unhandledRejection', (reason) => {
      logToFile(`UNHANDLED REJECTION: ${reason}`);
    });

    process.on('exit', (code) => {
      logToFile(`Process exiting with code ${code}`);
    });
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

// Rest of imports
import {
  ProjectResponse,
  SingleProjectResponse,
  PromptResponse,
  SinglePromptResponse,
  TraceResponse,
  SingleTraceResponse,
  TraceStatsResponse,
  MetricsResponse
} from './types.js';

// Import capabilities module
import {
  opikCapabilities,
  getEnabledCapabilities,
  getCapabilitiesDescription
} from './utils/capabilities.js';

// Helper function to make requests to API with file logging
const makeApiRequest = async <T>(
  path: string,
  options: RequestInit = {},
  workspaceName?: string
): Promise<{ data: T | null; error: string | null }> => {
  // Prepare headers based on configuration
  const API_HEADERS: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
    authorization: config.apiKey
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
      logToFile(`DEBUG - Workspace name before setting header: "${workspaceNameToUse}", type: ${typeof workspaceNameToUse}, length: ${workspaceNameToUse.length}`);

      // Use the raw workspace name - do not encode it
      API_HEADERS["Comet-Workspace"] = workspaceNameToUse;
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
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error occurred";
    logToFile(`Error making API request: ${errorMessage}`);
    return {
      data: null,
      error: errorMessage,
    };
  }
};

// Create and configure server - no console output here
const server = new McpServer({
  name: config.mcpName,
  version: config.mcpVersion,
}, {
  capabilities: {
    resources: {}, // Enable resources capability
    tools: {}      // Enable tools capability
  }
});

// Add resources to the MCP server
if (config.workspaceName) {
  // Define a workspace info resource
  server.resource(
    "workspace-info",
    "opik://workspace-info",
    async () => ({
      contents: [{
        uri: "opik://workspace-info",
        text: JSON.stringify({
          name: config.workspaceName,
          apiUrl: config.apiBaseUrl,
          selfHosted: config.isSelfHosted
        }, null, 2)
      }]
    })
  );

  // Define a projects resource that provides the list of projects in the workspace
  server.resource(
    "projects-list",
    "opik://projects-list",
    async () => {
      try {
        const response = await makeApiRequest<ProjectResponse>("/v1/private/projects");

        if (!response.data) {
          return {
            contents: [{
              uri: "opik://projects-list",
              text: `Error: ${response.error || "Unknown error fetching projects"}`
            }]
          };
        }

        return {
          contents: [{
            uri: "opik://projects-list",
            text: JSON.stringify(response.data, null, 2)
          }]
        };
      } catch (error) {
        logToFile(`Error fetching projects resource: ${error}`);
        return {
          contents: [{
            uri: "opik://projects-list",
            text: `Error: Failed to fetch projects data`
          }]
        };
      }
    }
  );
}

// DO NOT send any protocol messages before server initialization
// REMOVED: sendProtocolMessage("log", "Initializing Opik MCP Server");

// Conditionally enable tool categories based on configuration
if (config.mcpEnablePromptTools) {
  // ----------- PROMPTS TOOLS -----------

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
}

// ----------- PROJECTS/WORKSPACES TOOLS -----------
if (config.mcpEnableProjectTools) {
  server.tool(
    "list-projects",
    "Get a list of projects/workspaces",
    {
      page: z.number().describe("Page number for pagination"),
      size: z.number().describe("Number of items per page"),
      sortBy: z.string().optional().describe("Sort projects by this field"),
      sortOrder: z.string().optional().describe("Sort order (asc or desc)"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { page, size, sortBy, sortOrder, workspaceName } = args;

      // Build query string
      let url = `/v1/private/projects?page=${page}&size=${size}`;
      if (sortBy) url += `&sort_by=${sortBy}`;
      if (sortOrder) url += `&sort_order=${sortOrder}`;

      const response = await makeApiRequest<ProjectResponse>(url, {}, workspaceName);

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to fetch projects" },
          ],
        };
      }

      return {
        content: [
          {
            type: "text",
            text: `Found ${response.data.total} projects (showing page ${
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
    "get-project-by-id",
    "Get a single project by ID",
    {
      projectId: z.string().describe("ID of the project to fetch"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { projectId, workspaceName } = args;

      const response = await makeApiRequest<SingleProjectResponse>(
        `/v1/private/projects/${projectId}`,
        {},
        workspaceName
      );

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to fetch project" },
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
    "create-project",
    "Create a new project/workspace",
    {
      name: z.string().describe("Name of the project"),
      description: z.string().optional().describe("Description of the project"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { name, description, workspaceName } = args;
      const response = await makeApiRequest<void>(`/v1/private/projects`, {
        method: "POST",
        body: JSON.stringify({ name, description }),
      }, workspaceName);

      return {
        content: [
          {
            type: "text",
            text: response.error || "Successfully created project",
          },
        ],
      };
    }
  );

  server.tool(
    "update-project",
    "Update a project",
    {
      projectId: z.string().describe("ID of the project to update"),
      name: z.string().optional().describe("New project name"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
      description: z.string().optional().describe("New project description"),
    },
    async (args) => {
      const { projectId, name, description, workspaceName } = args;

      // Build update data
      const updateData: Record<string, any> = {};
      if (name !== undefined) updateData.name = name;
      if (description !== undefined) updateData.description = description;

      const response = await makeApiRequest<SingleProjectResponse>(
        `/v1/private/projects/${projectId}`,
        {
          method: "PATCH",
          body: JSON.stringify(updateData),
        },
        workspaceName
      );

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to update project" },
          ],
        };
      }

      return {
        content: [
          {
            type: "text",
            text: "Project successfully updated",
          },
          {
            type: "text",
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "delete-project",
    "Delete a project",
    {
      projectId: z.string().describe("ID of the project to delete"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { projectId, workspaceName } = args;
      const response = await makeApiRequest<void>(
        `/v1/private/projects/${projectId}`,
        {
          method: "DELETE",
        },
        workspaceName
      );

      return {
        content: [
          {
            type: "text",
            text: !response.error
              ? "Successfully deleted project"
              : response.error || "Failed to delete project",
          },
        ],
      };
    }
  );
}

// ----------- TRACES TOOLS -----------
if (config.mcpEnableTraceTools) {
  server.tool(
    "list-traces",
    "Get a list of traces",
    {
      page: z.number().describe("Page number for pagination"),
      size: z.number().describe("Number of items per page"),
      projectId: z.string().optional().describe("Project ID to filter traces"),
      projectName: z.string().optional().describe("Project name to filter traces"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { page, size, projectId, projectName, workspaceName } = args;
      let url = `/v1/private/traces?page=${page}&size=${size}`;

      // Add project filtering - API requires either project_id or project_name
      if (projectId) {
        url += `&project_id=${projectId}`;
      } else if (projectName) {
        url += `&project_name=${encodeURIComponent(projectName)}`;
      } else {
        // If no project specified, we need to find one for the API to work
        const projectsResponse = await makeApiRequest<ProjectResponse>(
          `/v1/private/projects?page=1&size=1`,
          {},
          workspaceName
        );

        if (projectsResponse.data &&
            projectsResponse.data.content &&
            projectsResponse.data.content.length > 0) {
          const firstProject = projectsResponse.data.content[0];
          url += `&project_id=${firstProject.id}`;
          logToFile(`No project specified, using first available: ${firstProject.name} (${firstProject.id})`);
        } else {
          return {
            content: [
              { type: "text", text: "Error: No project ID or name provided, and no projects found" },
            ],
          };
        }
      }

      const response = await makeApiRequest<TraceResponse>(url, {}, workspaceName);

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to fetch traces" },
          ],
        };
      }

      return {
        content: [
          {
            type: "text",
            text: `Found ${response.data.total} traces (showing page ${
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
    "get-trace-by-id",
    "Get a single trace by ID",
    {
      traceId: z.string().describe("ID of the trace to fetch"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { traceId, workspaceName } = args;
      const response = await makeApiRequest<SingleTraceResponse>(
        `/v1/private/traces/${traceId}`,
        {},
        workspaceName
      );

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to fetch trace" },
          ],
        };
      }

      // Format the response for better readability
      const formattedResponse: any = { ...response.data };

      // Format input/output if they're large
      if (formattedResponse.input && typeof formattedResponse.input === 'object' && Object.keys(formattedResponse.input).length > 0) {
        formattedResponse.input = JSON.stringify(formattedResponse.input, null, 2);
      }

      if (formattedResponse.output && typeof formattedResponse.output === 'object' && Object.keys(formattedResponse.output).length > 0) {
        formattedResponse.output = JSON.stringify(formattedResponse.output, null, 2);
      }

      return {
        content: [
          {
            type: "text",
            text: `Trace Details for ID: ${traceId}`,
          },
          {
            type: "text",
            text: JSON.stringify(formattedResponse, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get-trace-stats",
    "Get statistics for traces",
    {
      projectId: z.string().optional().describe("Project ID to filter traces"),
      projectName: z.string().optional().describe("Project name to filter traces"),
      startDate: z.string().optional().describe("Start date in ISO format (YYYY-MM-DD)"),
      endDate: z.string().optional().describe("End date in ISO format (YYYY-MM-DD)"),
      workspaceName: z.string().optional().describe("Workspace name to use instead of the default"),
    },
    async (args) => {
      const { projectId, projectName, startDate, endDate, workspaceName } = args;
      let url = `/v1/private/traces/stats`;

      // Build query parameters
      const queryParams = [];

      // Add project filtering - API requires either project_id or project_name
      if (projectId) {
        queryParams.push(`project_id=${projectId}`);
      } else if (projectName) {
        queryParams.push(`project_name=${encodeURIComponent(projectName)}`);
      } else {
        // If no project specified, we need to find one for the API to work
        const projectsResponse = await makeApiRequest<ProjectResponse>(
          `/v1/private/projects?page=1&size=1`,
          {},
          workspaceName
        );

        if (projectsResponse.data &&
            projectsResponse.data.content &&
            projectsResponse.data.content.length > 0) {
          const firstProject = projectsResponse.data.content[0];
          queryParams.push(`project_id=${firstProject.id}`);
          logToFile(`No project specified, using first available: ${firstProject.name} (${firstProject.id})`);
        } else {
          return {
            content: [
              { type: "text", text: "Error: No project ID or name provided, and no projects found" },
            ],
          };
        }
      }

      if (startDate) queryParams.push(`start_date=${startDate}`);
      if (endDate) queryParams.push(`end_date=${endDate}`);

      if (queryParams.length > 0) {
        url += `?${queryParams.join('&')}`;
      }

      const response = await makeApiRequest<TraceStatsResponse>(url, {}, workspaceName);

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to fetch trace statistics" },
          ],
        };
      }

      return {
        content: [
          {
            type: "text",
            text: `Trace Statistics:`,
          },
          {
            type: "text",
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );
}

// ----------- METRICS TOOLS -----------
if (config.mcpEnableMetricTools) {
  server.tool(
    "get-metrics",
    "Get metrics data",
    {
      metricName: z.string().optional().describe("Optional metric name to filter"),
      projectId: z.string().optional().describe("Optional project ID to filter metrics"),
      projectName: z.string().optional().describe("Optional project name to filter metrics"),
      startDate: z.string().optional().describe("Start date in ISO format (YYYY-MM-DD)"),
      endDate: z.string().optional().describe("End date in ISO format (YYYY-MM-DD)"),
    },
    async (args) => {
      const { metricName, projectId, projectName, startDate, endDate } = args;
      let url = `/v1/private/metrics`;

      const queryParams = [];
      if (metricName) queryParams.push(`metric_name=${metricName}`);

      // Add project filtering - API requires either project_id or project_name
      if (projectId) {
        queryParams.push(`project_id=${projectId}`);
      } else if (projectName) {
        queryParams.push(`project_name=${encodeURIComponent(projectName)}`);
      } else {
        // If no project specified, we need to find one for the API to work
        const projectsResponse = await makeApiRequest<ProjectResponse>(
          `/v1/private/projects?page=1&size=1`
        );

        if (projectsResponse.data &&
            projectsResponse.data.content &&
            projectsResponse.data.content.length > 0) {
          const firstProject = projectsResponse.data.content[0];
          queryParams.push(`project_id=${firstProject.id}`);
          logToFile(`No project specified, using first available: ${firstProject.name} (${firstProject.id})`);
        } else {
          return {
            content: [
              { type: "text", text: "Error: No project ID or name provided, and no projects found" },
            ],
          };
        }
      }

      if (startDate) queryParams.push(`start_date=${startDate}`);
      if (endDate) queryParams.push(`end_date=${endDate}`);

      if (queryParams.length > 0) {
        url += `?${queryParams.join('&')}`;
      }

      const response = await makeApiRequest<MetricsResponse>(url);

      if (!response.data) {
        return {
          content: [
            { type: "text", text: response.error || "Failed to fetch metrics" },
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
}

// ----------- SERVER CONFIGURATION TOOLS -----------

server.tool(
  "get-server-info",
  "Get information about the Opik server configuration",
  {
    random_string: z.string().optional().describe("Dummy parameter for no-parameter tools"),
  },
  async () => {
    // Get capabilities based on current configuration
    const capabilities = getEnabledCapabilities(config);
    const capabilitiesDescription = getCapabilitiesDescription(config);

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify({
            // API configuration
            apiBaseUrl: config.apiBaseUrl,
            isSelfHosted: config.isSelfHosted,
            hasWorkspace: !!config.workspaceName,
            workspaceName: config.workspaceName || "none",

            // MCP configuration
            mcpName: config.mcpName,
            mcpVersion: config.mcpVersion,
            mcpDefaultWorkspace: config.mcpDefaultWorkspace,
            enabledTools: {
              prompts: config.mcpEnablePromptTools,
              projects: config.mcpEnableProjectTools,
              traces: config.mcpEnableTraceTools,
              metrics: config.mcpEnableMetricTools
            },
            serverVersion: "v1",

            // Capabilities information
            capabilities: capabilities
          }, null, 2),
        },
        {
          type: "text",
          text: capabilitiesDescription
        }
      ],
    };
  }
);

// Add a new tool for contextual help about Opik capabilities
server.tool(
  "get-opik-help",
  "Get contextual help about Opik Comet's capabilities",
  {
    topic: z.string().describe("The topic to get help about (prompts, projects, traces, metrics, or general)"),
    subtopic: z.string().optional().describe("Optional subtopic for more specific help"),
  },
  async (args) => {
    const { topic, subtopic } = args;
    const capabilities = getEnabledCapabilities(config);

    // Normalize topic to lowercase
    const normalizedTopic = topic.toLowerCase();

    // Check if the topic is valid
    if (!['prompts', 'projects', 'traces', 'metrics', 'general'].includes(normalizedTopic)) {
      return {
        content: [
          {
            type: "text",
            text: `Invalid topic: ${topic}. Valid topics are: prompts, projects, traces, metrics, general.`
          }
        ]
      };
    }

    // Get the capabilities for the requested topic
    const topicCapabilities = capabilities[normalizedTopic as keyof typeof capabilities];

    if (!topicCapabilities) {
      return {
        content: [
          {
            type: "text",
            text: `No information available for topic: ${topic}`
          }
        ]
      };
    }

    // If it's a general topic request
    if (normalizedTopic === 'general') {
      return {
        content: [
          {
            type: "text",
            text: `Opik Comet General Information:\n\n` +
                  `API Version: ${(topicCapabilities as any).apiVersion}\n` +
                  `Authentication: ${(topicCapabilities as any).authentication}\n` +
                  `Rate Limit: ${(topicCapabilities as any).rateLimit}\n` +
                  `Supported Formats: ${(topicCapabilities as any).supportedFormats?.join(', ') || 'JSON'}`
          }
        ]
      };
    }

    // For other topics, check if they're available
    const typedCapabilities = topicCapabilities as any;
    if (!typedCapabilities.available) {
      return {
        content: [
          {
            type: "text",
            text: `${topic} functionality is not enabled in the current configuration.`
          }
        ]
      };
    }

    // If a subtopic is specified, provide more specific help
    if (subtopic) {
      const normalizedSubtopic = subtopic.toLowerCase();

      // Handle different subtopics
      switch (normalizedSubtopic) {
        case 'features':
          return {
            content: [
              {
                type: "text",
                text: `${topic} Features:\n\n` +
                      typedCapabilities.features.map((f: string) => `- ${f}`).join('\n')
              }
            ]
          };

        case 'limitations':
          return {
            content: [
              {
                type: "text",
                text: `${topic} Limitations:\n\n` +
                      typedCapabilities.limitations.map((l: string) => `- ${l}`).join('\n')
              }
            ]
          };

        case 'examples':
          if (typedCapabilities.examples && typedCapabilities.examples.length > 0) {
            return {
              content: [
                {
                  type: "text",
                  text: `${topic} Examples:\n\n` +
                        typedCapabilities.examples.map((e: string) => `- ${e}`).join('\n')
                }
              ]
            };
          } else {
            return {
              content: [
                {
                  type: "text",
                  text: `No examples available for ${topic}.`
                }
              ]
            };
          }

        case 'schema':
          if (typedCapabilities.schema) {
            return {
              content: [
                {
                  type: "text",
                  text: `${topic} Schema:\n\n` +
                        JSON.stringify(typedCapabilities.schema, null, 2)
                }
              ]
            };
          } else {
            return {
              content: [
                {
                  type: "text",
                  text: `No schema information available for ${topic}.`
                }
              ]
            };
          }

        default:
          // Check if the subtopic is a property of the capabilities
          if (typedCapabilities[normalizedSubtopic] !== undefined) {
            const value = typedCapabilities[normalizedSubtopic];

            // Format the value based on its type
            let formattedValue = '';
            if (Array.isArray(value)) {
              formattedValue = value.map((v: any) => `- ${v}`).join('\n');
            } else if (typeof value === 'object') {
              formattedValue = JSON.stringify(value, null, 2);
            } else {
              formattedValue = value.toString();
            }

            return {
              content: [
                {
                  type: "text",
                  text: `${topic} ${subtopic}:\n\n${formattedValue}`
                }
              ]
            };
          } else {
            return {
              content: [
                {
                  type: "text",
                  text: `Invalid subtopic: ${subtopic} for topic: ${topic}`
                }
              ]
            };
          }
      }
    }

    // If no subtopic is specified, provide general information about the topic
    let response = `${topic.charAt(0).toUpperCase() + topic.slice(1)} Capabilities:\n\n`;

    response += "Features:\n";
    typedCapabilities.features.forEach((feature: string) => {
      response += `- ${feature}\n`;
    });

    response += "\nLimitations:\n";
    typedCapabilities.limitations.forEach((limitation: string) => {
      response += `- ${limitation}\n`;
    });

    // Add topic-specific information
    switch (normalizedTopic) {
      case 'prompts':
        response += `\nVersion Control: ${typedCapabilities.versionControl ? 'Supported' : 'Not Supported'}\n`;
        response += `Template Format: ${typedCapabilities.templateFormat}\n`;
        break;

      case 'projects':
        response += `\nHierarchy Support: ${typedCapabilities.hierarchySupport ? 'Supported' : 'Not Supported'}\n`;
        response += `Sharing Support: ${typedCapabilities.sharingSupport ? 'Supported' : 'Not Supported'}\n`;
        break;

      case 'traces':
        response += `\nData Retention: ${typedCapabilities.dataRetention}\n`;
        response += `Search Capabilities:\n`;
        typedCapabilities.searchCapabilities.forEach((capability: string) => {
          response += `- ${capability}\n`;
        });
        break;

      case 'metrics':
        response += `\nAvailable Metrics:\n`;
        typedCapabilities.availableMetrics.forEach((metric: string) => {
          response += `- ${metric}\n`;
        });
        response += `Custom Metrics Support: ${typedCapabilities.customMetricsSupport ? 'Supported' : 'Not Supported'}\n`;
        response += `Visualization Support: ${typedCapabilities.visualizationSupport ? 'Supported' : 'Not Supported'}\n`;
        break;
    }

    // Add examples if available
    if (typedCapabilities.examples && typedCapabilities.examples.length > 0) {
      response += `\nExamples:\n`;
      typedCapabilities.examples.forEach((example: string) => {
        response += `- ${example}\n`;
      });
    }

    return {
      content: [
        {
          type: "text",
          text: response
        }
      ]
    };
  }
);

// Add a tool for providing contextual examples of how to use Opik Comet
server.tool(
  "get-opik-examples",
  "Get examples of how to use Opik Comet's API for specific tasks",
  {
    task: z.string().describe("The task to get examples for (e.g., 'create prompt', 'analyze traces', 'monitor costs')"),
  },
  async (args) => {
    const { task } = args;
    const normalizedTask = task.toLowerCase();

    // Define example categories and their corresponding examples
    interface ExampleData {
      description: string;
      steps: string[];
      code: string;
    }

    const examples: Record<string, ExampleData> = {
      // Prompt-related examples
      "create prompt": {
        description: "Creating a new prompt template in Opik Comet",
        steps: [
          "1. Use the 'create-prompt' tool to create a new prompt with a name",
          "2. Use the 'create-prompt-version' tool to add content to the prompt",
          "3. Retrieve the prompt using 'get-prompt-by-id' to verify it was created"
        ],
        code: `// Example: Creating a customer service prompt
const promptName = "Customer Service Greeting";
const promptTemplate = "Hello {{customer_name}}, thank you for contacting our support. How can I help you today?";
const commitMessage = "Initial version of customer service greeting";

// First create the prompt
const createResult = await mcp.createPrompt({ name: promptName });
const promptId = createResult.id;

// Then add content as a version
await mcp.createPromptVersion({
  name: promptName,
  template: promptTemplate,
  commit_message: commitMessage
});`
      },

      "version prompt": {
        description: "Creating a new version of an existing prompt",
        steps: [
          "1. Use the 'list-prompts' tool to find the prompt you want to version",
          "2. Use the 'create-prompt-version' tool to add a new version with updated content",
          "3. Include a descriptive commit message explaining the changes"
        ],
        code: `// Example: Creating a new version of an existing prompt
const promptName = "Customer Service Greeting";
const newTemplate = "Hello {{customer_name}}, thank you for reaching out to our support team. How may I assist you today?";
const commitMessage = "Improved wording for more professional tone";

await mcp.createPromptVersion({
  name: promptName,
  template: newTemplate,
  commit_message: commitMessage
});`
      },

      // Project-related examples
      "create project": {
        description: "Creating a new project in Opik Comet",
        steps: [
          "1. Use the 'create-project' tool to create a new project with a name and description",
          "2. Retrieve the project using 'get-project-by-id' to verify it was created"
        ],
        code: `// Example: Creating a new project
const projectName = "Customer Support Bot";
const projectDescription = "AI assistant for handling customer support inquiries";

const createResult = await mcp.createProject({
  name: projectName,
  description: projectDescription
});

// The project ID will be in the response
const projectId = createResult.id;`
      },

      "organize traces": {
        description: "Organizing traces by project",
        steps: [
          "1. Create projects for different use cases or applications",
          "2. When recording traces, associate them with the appropriate project",
          "3. Use the 'list-traces' tool with project filtering to view traces for a specific project"
        ],
        code: `// Example: Listing traces for a specific project
const projectId = "proj_12345";
const page = 1;
const size = 10;

const traces = await mcp.listTraces({
  page: page,
  size: size,
  projectId: projectId
});

// Alternatively, you can filter by project name
const projectName = "Customer Support Bot";
const tracesByName = await mcp.listTraces({
  page: page,
  size: size,
  projectName: projectName
});`
      },

      // Trace-related examples
      "log trace": {
        description: "Logging a trace with the Opik API",
        steps: [
          "1. Create a trace with input and output data",
          "2. Add spans to the trace to capture detailed steps",
          "3. Include LLM calls with relevant metadata"
        ],
        code: `// Example: Logging a trace with spans
// Based on official Opik documentation

// Python SDK example (for reference)
/*
from opik import Opik

client = Opik(project_name="Opik client demo")

# Create a trace
trace = client.trace(
  name="my_trace",
  input={"user_question": "Hello, how are you?"},
  output={"response": "Comment ça va?"}
)

# Add a span
trace.span(
  name="Add prompt template",
  input={"text": "Hello, how are you?", "prompt_template": "Translate the following text to French: {text}"},
  output={"text": "Translate the following text to French: hello, how are you?"}
)

# Add an LLM call
trace.span(
  name="llm_call",
  type="llm",
  input={"prompt": "Translate the following text to French: hello, how are you?"},
  output={"response": "Comment ça va?"}
)
*/

// JavaScript/TypeScript equivalent using the API
const projectId = "proj_12345";

// Create a trace
const traceData = {
  name: "my_trace",
  project_id: projectId,
  input: {"user_question": "Hello, how are you?"},
  output: {"response": "Comment ça va?"}
};

const traceResponse = await fetch("/v1/private/traces", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "YOUR_API_KEY"
  },
  body: JSON.stringify(traceData)
});

const trace = await traceResponse.json();
const traceId = trace.id;

// Add spans to the trace
const span1 = {
  trace_id: traceId,
  name: "Add prompt template",
  input: {"text": "Hello, how are you?", "prompt_template": "Translate the following text to French: {text}"},
  output: {"text": "Translate the following text to French: hello, how are you?"}
};

const span2 = {
  trace_id: traceId,
  name: "llm_call",
  type: "llm",
  input: {"prompt": "Translate the following text to French: hello, how are you?"},
  output: {"response": "Comment ça va?"}
};

await fetch("/v1/private/spans", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "YOUR_API_KEY"
  },
  body: JSON.stringify(span1)
});

await fetch("/v1/private/spans", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "YOUR_API_KEY"
  },
  body: JSON.stringify(span2)
});`
      },

      "analyze traces": {
        description: "Analyzing trace data to understand usage patterns",
        steps: [
          "1. Use the 'list-traces' tool to retrieve traces for a specific project",
          "2. Use the 'get-trace-stats' tool to get aggregated statistics",
          "3. Filter by date range to analyze trends over time"
        ],
        code: `// Example: Getting trace statistics for a date range
const projectId = "proj_12345";
const startDate = "2023-01-01";
const endDate = "2023-01-31";

const stats = await mcp.getTraceStats({
  projectId: projectId,
  startDate: startDate,
  endDate: endDate
});

// The response will include aggregated data like:
// - Total trace count
// - Total token usage
// - Cost information
// - Daily breakdowns`
      },

      "view trace details": {
        description: "Viewing detailed information about a specific trace",
        steps: [
          "1. Use the 'list-traces' tool to find the trace you want to examine",
          "2. Use the 'get-trace-by-id' tool with the trace ID to get detailed information",
          "3. Analyze the input, output, and metadata to understand the interaction"
        ],
        code: `// Example: Getting detailed information about a trace
const traceId = "trace_67890";

const traceDetails = await mcp.getTraceById({
  traceId: traceId
});

// The response will include:
// - Input and output data
// - Token usage
// - Timestamps
// - Metadata
// - Cost information
// - Spans (detailed steps within the trace)`
      },

      "annotate trace": {
        description: "Annotating a trace with feedback scores",
        steps: [
          "1. Retrieve a trace using 'get-trace-by-id'",
          "2. Add feedback scores to evaluate the trace quality",
          "3. Use the feedback for monitoring and improvement"
        ],
        code: `// Example: Annotating a trace with feedback scores
// Based on Opik documentation

// Python SDK example (for reference)
/*
from opik import Opik

client = Opik(project_name="Opik client demo")

# Get an existing trace
trace = client.get_trace(trace_id="trace_12345")

# Add feedback scores
trace.add_feedback_score(name="relevance", score=0.8)
trace.add_feedback_score(name="accuracy", score=0.9)
trace.add_feedback_score(name="helpfulness", score=0.7)
*/

// JavaScript/TypeScript equivalent using the API
const traceId = "trace_12345";

// Add feedback scores to the trace
const feedbackData = {
  scores: [
    { name: "relevance", score: 0.8 },
    { name: "accuracy", score: 0.9 },
    { name: "helpfulness", score: 0.7 }
  ]
};

await fetch(\`/v1/private/traces/\${traceId}/feedback\`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "YOUR_API_KEY"
  },
  body: JSON.stringify(feedbackData)
});`
      },

      // Metrics-related examples
      "monitor costs": {
        description: "Monitoring costs across projects and time periods",
        steps: [
          "1. Use the 'get-metrics' tool with the 'cost' metric name",
          "2. Filter by project and date range to focus on specific usage",
          "3. Analyze trends to identify cost patterns"
        ],
        code: `// Example: Monitoring costs for a specific project
const projectId = "proj_12345";
const metricName = "cost";
const startDate = "2023-01-01";
const endDate = "2023-01-31";

const costMetrics = await mcp.getMetrics({
  metricName: metricName,
  projectId: projectId,
  startDate: startDate,
  endDate: endDate
});

// The response will include cost data points over time`
      },

      "track token usage": {
        description: "Tracking token usage across different models and projects",
        steps: [
          "1. Use the 'get-metrics' tool with token-related metric names",
          "2. Filter by project and date range to focus on specific usage",
          "3. Compare prompt tokens vs. completion tokens to optimize usage"
        ],
        code: `// Example: Tracking token usage metrics
const projectId = "proj_12345";
const startDate = "2023-01-01";
const endDate = "2023-01-31";

// Get total token usage
const totalTokens = await mcp.getMetrics({
  metricName: "total_tokens",
  projectId: projectId,
  startDate: startDate,
  endDate: endDate
});

// Get prompt token usage
const promptTokens = await mcp.getMetrics({
  metricName: "prompt_tokens",
  projectId: projectId,
  startDate: startDate,
  endDate: endDate
});

// Get completion token usage
const completionTokens = await mcp.getMetrics({
  metricName: "completion_tokens",
  projectId: projectId,
  startDate: startDate,
  endDate: endDate
});`
      },

      "evaluate llm": {
        description: "Evaluating LLM outputs using Opik's evaluation metrics",
        steps: [
          "1. Set up evaluation metrics for your use case",
          "2. Apply metrics to trace data to measure performance",
          "3. Analyze results to identify areas for improvement"
        ],
        code: `// Example: Evaluating LLM outputs with metrics
// Based on Opik documentation

// Python SDK example (for reference)
/*
from opik import evaluate
from opik.metrics import Hallucination, AnswerRelevance, ContextPrecision

# Define evaluation metrics
metrics = [
  Hallucination(),
  AnswerRelevance(),
  ContextPrecision()
]

# Evaluate a response
result = evaluate(
  question="What is the capital of France?",
  answer="Paris is the capital of France.",
  context=["Paris is the capital and most populous city of France."],
  metrics=metrics
)

# Print results
print(result.scores)
*/

// JavaScript/TypeScript equivalent using the API
const evaluationData = {
  question: "What is the capital of France?",
  answer: "Paris is the capital of France.",
  context: ["Paris is the capital and most populous city of France."],
  metrics: ["hallucination", "answer_relevance", "context_precision"]
};

const evaluationResponse = await fetch("/v1/private/evaluate", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "YOUR_API_KEY"
  },
  body: JSON.stringify(evaluationData)
});

const evaluationResults = await evaluationResponse.json();
// The response will include scores for each metric`
      }
    };

    // Find the closest matching example
    let bestMatch: string | null = null;
    let bestMatchScore = 0;

    for (const [key, example] of Object.entries(examples)) {
      // Simple matching algorithm - check if the normalized task contains the key
      if (normalizedTask.includes(key)) {
        const score = key.length; // Longer matches are better
        if (score > bestMatchScore) {
          bestMatch = key;
          bestMatchScore = score;
        }
      }
    }

    // If no match found, provide a list of available examples
    if (!bestMatch) {
      return {
        content: [
          {
            type: "text",
            text: `No specific example found for "${task}". Available example categories include:\n\n` +
                  Object.keys(examples).map(key => `- ${key}`).join('\n') +
                  `\n\nTry asking for one of these specific tasks.`
          }
        ]
      };
    }

    // Return the matched example
    const matchedExample = examples[bestMatch];

    return {
      content: [
        {
          type: "text",
          text: `Example: ${bestMatch}\n\n` +
                `Description: ${matchedExample.description}\n\n` +
                `Steps:\n${matchedExample.steps.join('\n')}\n\n` +
                `Code Example:\n\`\`\`javascript\n${matchedExample.code}\n\`\`\``
        }
      ]
    };
  }
);

// Add a tool for providing information about Opik's tracing capabilities
server.tool(
  "get-opik-tracing-info",
  "Get information about Opik's tracing capabilities and how to use them",
  {
    topic: z.string().optional().describe("Optional specific tracing topic to get information about (e.g., 'spans', 'distributed', 'multimodal', 'annotations')"),
  },
  async (args) => {
    const { topic } = args;

    // Define tracing information
    interface TracingInfo {
      name: string;
      description: string;
      key_features: string[];
      use_cases: string[];
      example?: string;
      related_topics?: string[];
    }

    const tracingInfo: Record<string, TracingInfo> = {
      "basic": {
        name: "Basic Tracing",
        description: "Core tracing functionality for recording LLM interactions with input and output data.",
        key_features: [
          "Record input and output for LLM calls",
          "Track token usage and costs",
          "Organize traces by project",
          "Add metadata to traces"
        ],
        use_cases: [
          "Monitoring LLM usage in applications",
          "Debugging LLM-based systems",
          "Cost tracking and optimization",
          "Performance monitoring"
        ],
        example: `from opik import Opik

# Initialize Opik client
client = Opik(project_name="My Project")

# Create a trace
trace = client.trace(
    name="simple_query",
    input={"question": "What is the capital of France?"},
    output={"answer": "The capital of France is Paris."},
    metadata={"model": "gpt-4", "temperature": 0.7}
)`,
        related_topics: ["spans", "annotations", "metadata"]
      },

      "spans": {
        name: "Spans",
        description: "Detailed tracking of steps within a trace to capture the full flow of an LLM interaction.",
        key_features: [
          "Break down traces into logical steps",
          "Track intermediate processing",
          "Capture the full chain of operations",
          "Measure performance of individual steps"
        ],
        use_cases: [
          "Debugging complex LLM pipelines",
          "Performance optimization of multi-step processes",
          "Visualizing the flow of information",
          "Identifying bottlenecks in processing"
        ],
        example: `from opik import Opik

# Initialize Opik client
client = Opik(project_name="RAG Application")

# Create a trace
trace = client.trace(
    name="rag_query",
    input={"question": "What is the capital of France?"},
    output={"answer": "The capital of France is Paris."}
)

# Add spans for each step in the process
trace.span(
    name="query_processing",
    input={"raw_query": "What is the capital of France?"},
    output={"processed_query": "capital France"}
)

trace.span(
    name="document_retrieval",
    input={"query": "capital France"},
    output={"documents": ["Paris is the capital of France.", "France is a country in Europe."]}
)

trace.span(
    name="llm_generation",
    type="llm",
    input={"prompt": "Based on these documents, answer: What is the capital of France?\\n\\nDocuments:\\n- Paris is the capital of France.\\n- France is a country in Europe."},
    output={"response": "The capital of France is Paris."}
)`,
        related_topics: ["basic", "distributed", "context"]
      },

      "distributed": {
        name: "Distributed Tracing",
        description: "Tracing across multiple services or components in a distributed system.",
        key_features: [
          "Track LLM interactions across service boundaries",
          "Maintain context across different components",
          "Visualize end-to-end flows",
          "Correlate related traces"
        ],
        use_cases: [
          "Microservices architectures with LLMs",
          "Complex multi-component AI systems",
          "Cross-service debugging",
          "End-to-end performance monitoring"
        ],
        example: `# Service 1: Initial request handler
from opik import Opik, opik_context

client = Opik(project_name="Distributed System")

# Create a trace
trace = client.trace(
    name="user_request",
    input={"user_query": "What is the capital of France?"}
)

# Get trace headers to pass to the next service
trace_headers = opik_context.get_distributed_trace_headers()

# Pass trace_headers to Service 2 via API call, message queue, etc.

# -----------------------------------------------

# Service 2: Document retrieval service
from opik import Opik, opik_context

client = Opik(project_name="Distributed System")

# Initialize context from received headers
opik_context.init_from_headers(received_headers)

# This span will be automatically associated with the parent trace
with client.span(name="document_retrieval") as span:
    # Retrieve documents
    documents = retrieve_documents("capital France")
    span.update(output={"documents": documents})

# -----------------------------------------------

# Service 3: LLM service
from opik import Opik, opik_context

client = Opik(project_name="Distributed System")

# Initialize context from received headers
opik_context.init_from_headers(received_headers)

# This span will be automatically associated with the parent trace
with client.span(name="llm_generation", type="llm") as span:
    # Generate response
    response = generate_llm_response(documents, "What is the capital of France?")
    span.update(output={"response": response})

# Back in Service 1, update the trace with the final output
trace.update(output={"answer": "The capital of France is Paris."})`,
        related_topics: ["spans", "context", "opentelemetry"]
      },

      "multimodal": {
        name: "Multimodal Tracing",
        description: "Tracing for LLM interactions that involve multiple modalities like text, images, and audio.",
        key_features: [
          "Track inputs and outputs across modalities",
          "Support for image, audio, and text data",
          "Visualize multimodal interactions",
          "Analyze performance across modalities"
        ],
        use_cases: [
          "Vision-language models (VLMs)",
          "Image generation and analysis",
          "Audio transcription and processing",
          "Multimodal chatbots and assistants"
        ],
        example: `from opik import Opik
import base64

# Initialize Opik client
client = Opik(project_name="Multimodal App")

# Load image as base64
with open("image.jpg", "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")

# Create a multimodal trace
trace = client.trace(
    name="image_analysis",
    input={
        "image": {"mime_type": "image/jpeg", "data": image_data},
        "question": "What objects are in this image?"
    },
    output={"answer": "The image contains a cat sitting on a windowsill."}
)

# Add a span for the vision model
trace.span(
    name="vision_model",
    type="llm",
    input={"image": {"mime_type": "image/jpeg", "data": image_data}},
    output={"description": "A tabby cat sitting on a wooden windowsill looking outside."}
)

# Add a span for the text generation
trace.span(
    name="text_generation",
    type="llm",
    input={"prompt": "Based on this description: 'A tabby cat sitting on a wooden windowsill looking outside.', answer: What objects are in this image?"},
    output={"answer": "The image contains a cat sitting on a windowsill."}
)`,
        related_topics: ["basic", "spans"]
      },

      "annotations": {
        name: "Trace Annotations",
        description: "Adding feedback scores and annotations to traces for evaluation and improvement.",
        key_features: [
          "Add qualitative and quantitative feedback",
          "Score trace quality and performance",
          "Track user satisfaction",
          "Support continuous improvement"
        ],
        use_cases: [
          "Quality monitoring in production",
          "User feedback collection",
          "A/B testing of LLM configurations",
          "Performance benchmarking"
        ],
        example: `from opik import Opik

# Initialize Opik client
client = Opik(project_name="Customer Support")

# Get an existing trace
trace = client.get_trace(trace_id="trace_12345")

# Add feedback scores
trace.add_feedback_score(name="relevance", score=0.8)
trace.add_feedback_score(name="accuracy", score=0.9)
trace.add_feedback_score(name="helpfulness", score=0.7)

# Add a qualitative annotation
trace.add_annotation(text="Response was helpful but could be more concise.")`,
        related_topics: ["basic", "evaluation"]
      },

      "context": {
        name: "Context Management",
        description: "Managing trace context throughout the execution flow of an application.",
        key_features: [
          "Automatic context propagation",
          "Access current trace and span data",
          "Update traces and spans dynamically",
          "Support for async and concurrent operations"
        ],
        use_cases: [
          "Complex application flows",
          "Asynchronous processing",
          "Middleware integration",
          "Framework integration"
        ],
        example: `from opik import Opik, opik_context

# Initialize Opik client
client = Opik(project_name="Context Demo")

# Create a trace
with client.trace(name="main_process") as trace:
    # The trace is automatically set as the current trace

    # Access current trace data
    trace_data = opik_context.get_current_trace_data()

    # Create a span
    with client.span(name="subprocess") as span:
        # The span is automatically set as the current span

        # Access current span data
        span_data = opik_context.get_current_span_data()

        # Update the current span
        opik_context.update_current_span(output={"result": "Processed data"})

    # Update the current trace
    opik_context.update_current_trace(output={"final_result": "Complete"})`,
        related_topics: ["distributed", "spans"]
      },

      "opentelemetry": {
        name: "OpenTelemetry Integration",
        description: "Integration with the OpenTelemetry standard for distributed tracing.",
        key_features: [
          "Compatibility with OpenTelemetry ecosystem",
          "Standard-compliant trace format",
          "Integration with existing observability tools",
          "Support for mixed tracing environments"
        ],
        use_cases: [
          "Enterprise observability platforms",
          "Integration with existing monitoring systems",
          "Standardized tracing across organizations",
          "Multi-vendor observability solutions"
        ],
        example: `# OpenTelemetry integration example
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opik.integrations.opentelemetry import OpikSpanProcessor

# Set up OpenTelemetry
tracer_provider = TracerProvider()
trace.set_tracer_provider(tracer_provider)

# Set up OTLP exporter
otlp_exporter = OTLPSpanExporter(endpoint="your-otlp-endpoint")
tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

# Add Opik span processor
opik_processor = OpikSpanProcessor(
    project_name="OpenTelemetry Demo",
    api_key="your-opik-api-key"
)
tracer_provider.add_span_processor(opik_processor)

# Now OpenTelemetry traces will also be sent to Opik
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("main_operation") as span:
    # This span will be captured by both OpenTelemetry and Opik
    span.set_attribute("operation.type", "query")

    # Perform operations
    result = process_data()

    span.set_attribute("operation.result", result)`,
        related_topics: ["distributed", "context"]
      },

      "metadata": {
        name: "Trace Metadata",
        description: "Adding contextual metadata to traces for richer analysis and filtering.",
        key_features: [
          "Add custom metadata to traces and spans",
          "Tag traces for easier filtering",
          "Include environment and version information",
          "Track business-specific metrics"
        ],
        use_cases: [
          "Environment-specific analysis",
          "Version comparison",
          "Business impact tracking",
          "Custom categorization"
        ],
        example: `from opik import Opik

# Initialize Opik client
client = Opik(project_name="Metadata Demo")

# Create a trace with rich metadata
trace = client.trace(
    name="product_search",
    input={"query": "blue running shoes"},
    output={"results": ["Product 1", "Product 2", "Product 3"]},
    metadata={
        "environment": "production",
        "version": "1.2.3",
        "user_segment": "premium",
        "region": "us-west",
        "experiment_id": "exp_a1b2c3",
        "business_metrics": {
            "conversion_rate": 0.12,
            "average_order_value": 85.50
        }
    }
)

# Add tags for easier filtering
trace.add_tags(["search", "product", "footwear"])`,
        related_topics: ["basic", "annotations"]
      }
    };

    // If a specific topic is requested, return information about that topic
    if (topic) {
      const normalizedTopic = topic.toLowerCase();

      // Try exact match first
      if (tracingInfo[normalizedTopic]) {
        const topicData = tracingInfo[normalizedTopic];

        return {
          content: [
            {
              type: "text",
              text: `# ${topicData.name}\n\n` +
                    `**Description:** ${topicData.description}\n\n` +
                    `**Key Features:**\n${topicData.key_features.map(f => `- ${f}`).join('\n')}\n\n` +
                    `**Use Cases:**\n${topicData.use_cases.map(uc => `- ${uc}`).join('\n')}\n\n` +
                    (topicData.example ?
                      `**Example:**\n\`\`\`python\n${topicData.example}\n\`\`\`\n\n` :
                      '') +
                    (topicData.related_topics && topicData.related_topics.length > 0 ?
                      `**Related Topics:** ${topicData.related_topics.map(t => `\`${t}\``).join(', ')}` :
                      '')
            }
          ]
        };
      }

      // Try fuzzy match
      const fuzzyMatches = Object.keys(tracingInfo).filter(k =>
        k.includes(normalizedTopic) || normalizedTopic.includes(k)
      );

      if (fuzzyMatches.length > 0) {
        return {
          content: [
            {
              type: "text",
              text: `No exact match found for "${topic}". Did you mean one of these?\n\n` +
                    fuzzyMatches.map(m => `- ${tracingInfo[m].name}`).join('\n')
            }
          ]
        };
      }

      // No matches
      return {
        content: [
          {
            type: "text",
            text: `No information found for tracing topic "${topic}". Available topics include:\n\n` +
                  Object.values(tracingInfo).map(t => `- ${t.name}`).join('\n')
          }
        ]
      };
    }

    // If no specific topic is requested, return an overview of all tracing capabilities
    return {
      content: [
        {
          type: "text",
          text: `# Opik Tracing Capabilities\n\n` +
                `Opik provides comprehensive tracing capabilities for LLM applications, allowing you to track, analyze, and improve your AI systems.\n\n` +

                `## Core Tracing Features\n\n` +
                Object.values(tracingInfo)
                  .map(t => `### ${t.name}\n${t.description}\n\n**Key Features:**\n${t.key_features.map(f => `- ${f}`).join('\n')}\n`)
                  .join('\n\n') +

                `\n\n## Getting Started with Tracing\n\n` +
                `To start using Opik's tracing capabilities:\n\n` +
                `1. Install the Opik SDK: \`pip install opik\`\n` +
                `2. Configure your API key: \`opik configure\`\n` +
                `3. Create your first trace using the \`trace()\` method\n` +
                `4. Add spans to capture detailed steps in your process\n` +
                `5. View your traces in the Opik dashboard\n\n` +

                `For detailed information about a specific tracing topic, use this tool with the \`topic\` parameter.`
        }
      ]
    };
  }
);

// Server startup
async function main() {
  try {
    logToFile("Starting main function");

    // Initialize transport with error handling
    logToFile("Creating StdioServerTransport");
    const transport = new StdioServerTransport();

    // Add explicit error handlers to the transport
    transport.onerror = (error) => {
      logToFile(`Transport error: ${error.message}`);
    };

    transport.onclose = () => {
      logToFile("Transport connection closed");
    };

    // Log configuration for debugging purposes only to file
    logToFile(`API Base URL: ${config.apiBaseUrl}`);
    logToFile(`Self-hosted: ${config.isSelfHosted ? "Yes" : "No"}`);
    logToFile(`Workspace: ${config.workspaceName || "None"}`);

    // Test API call if test flag is set
    if (process.argv.includes("--test")) {
      logToFile("Test flag detected, making test API call");

      // First, let's try to list available workspaces
      logToFile("Listing available workspaces");
      const workspacesResult = await makeApiRequest<any>('/v1/private/workspaces');
      logToFile(`Workspaces API call result: ${JSON.stringify(workspacesResult, null, 2)}`);

      // Then try the projects API
      const testResult = await makeApiRequest<any>('/v1/private/projects?page=1&size=10');
      logToFile(`Projects API call result: ${JSON.stringify(testResult, null, 2)}`);

      process.exit(0);
    }

    try {
      // Connect server to transport - This is where the initialization handshake happens
      logToFile("Connecting server to transport");
      await server.connect(transport);

      logToFile("Transport connection established");

      // Success message AFTER transport is connected
      sendProtocolMessage("log", "Opik MCP Server successfully connected and running");

      logToFile("Opik MCP Server running on stdio");
      logToFile("Main function completed successfully");

      // Keep the process alive with a heartbeat
      setInterval(() => {
        logToFile("Heartbeat ping");
      }, 5000);

    } catch (connectError: any) {
      logToFile(`Error in server connection: ${connectError?.message || connectError}`);
      process.exit(1);
    }

  } catch (mainError: any) {
    logToFile(`Error in main function: ${mainError?.message || mainError}`);
    process.exit(1);
  }
}

main().catch((error) => {
  logToFile(`Fatal error in main() catch handler: ${error?.message || error}`);
  sendProtocolMessage("log", `Fatal error: ${error?.message || error}`);
  process.exit(1);
});

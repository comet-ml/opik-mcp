/**
 * Test client for Opik API
 * This helps test API calls and view the responses
 */

// Load environment variables first
import './utils/env.js';
import config from './config.js';

// Type definitions for API responses
import {
  ProjectResponse,
  TraceResponse,
  TraceStatsResponse,
  MetricsResponse,
  PromptResponse,
  SingleProjectResponse,
  SingleTraceResponse,
} from './types.js';

// Simple test client for MCP
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

/**
 * Make an API request to the Opik API
 */
async function makeApiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<{ data: T | null; error: string | null }> {
  // Prepare headers based on configuration
  // According to the documentation:
  // - authorization header should NOT include "Bearer" prefix
  // - Comet-Workspace header should be included for cloud installations
  const API_HEADERS: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    authorization: config.apiKey,
  };

  // Add workspace header for cloud version (and on-premise installations of Comet platform)
  if (config.workspaceName) {
    API_HEADERS['Comet-Workspace'] = config.workspaceName;

    if (config.debugMode) {
      console.log(`Using workspace: ${config.workspaceName}`);
    }
  }

  const url = `${config.apiBaseUrl}${path}`;
  console.log(`Making API request to: ${url}`);
  if (config.debugMode) {
    console.log('Headers:', JSON.stringify(API_HEADERS, null, 2));
  }

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...API_HEADERS,
        ...options.headers,
      },
    });

    // Get the response body text
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
      return {
        data: null,
        error: `HTTP error! status: ${response.status} ${JSON.stringify(responseData)}`,
      };
    }

    return {
      data: responseData as T,
      error: null,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    console.error('Error making API request:', error);
    return {
      data: null,
      error: errorMessage,
    };
  }
}

/**
 * Test functions for different API endpoints
 */
const api = {
  // Simple test endpoint
  async testConnection() {
    // Test with the projects endpoint which we know works
    return makeApiRequest<any>('/v1/private/projects');
  },

  // Workspaces
  async listWorkspaces() {
    return makeApiRequest<any>('/v1/private/workspaces');
  },

  // Projects
  async listProjects(page = 1, size = 10) {
    return makeApiRequest<ProjectResponse>(`/v1/private/projects?page=${page}&size=${size}`);
  },

  async getProject(projectId: string) {
    return makeApiRequest<SingleProjectResponse>(`/v1/private/projects/${projectId}`);
  },

  // Traces
  async listTraces(page = 1, size = 10, projectId?: string) {
    let url = `/v1/private/traces?page=${page}&size=${size}`;
    if (projectId) url += `&project_id=${projectId}`;
    return makeApiRequest<TraceResponse>(url);
  },

  async getTrace(traceId: string) {
    return makeApiRequest<SingleTraceResponse>(`/v1/private/traces/${traceId}`);
  },

  async getTraceStats(projectId?: string) {
    let url = `/v1/private/traces/stats`;
    if (projectId) url += `?project_id=${projectId}`;
    return makeApiRequest<TraceStatsResponse>(url);
  },

  // Prompts
  async listPrompts(page = 1, size = 10) {
    return makeApiRequest<PromptResponse>(`/v1/private/prompts?page=${page}&size=${size}`);
  },

  // Metrics
  async getMetrics(metricName?: string, projectId?: string) {
    const params = [];
    if (metricName) params.push(`metric_name=${metricName}`);
    if (projectId) params.push(`project_id=${projectId}`);

    let url = `/v1/private/metrics`;
    if (params.length > 0) {
      url += `?${params.join('&')}`;
    }

    return makeApiRequest<MetricsResponse>(url);
  },
};

/**
 * Find workspaces and projects with traces
 */
async function findWorkspacesAndProjects() {
  // Define the types for our results
  type ProjectWithTraces = {
    workspaceName: string;
    projectId: string;
    projectName: string;
    traceCount: number;
  };

  const results: {
    availableWorkspaces: any[];
    projectsWithTraces: ProjectWithTraces[];
  } = {
    availableWorkspaces: [],
    projectsWithTraces: [],
  };

  try {
    // Try the predefined workspace first (from config)
    if (config.workspaceName) {
      console.log(`Using predefined workspace: ${config.workspaceName}`);

      // List projects in this workspace
      const projectsResponse = await api.listProjects();
      if (projectsResponse.data && projectsResponse.data.content) {
        // Check each project for traces
        for (const project of projectsResponse.data.content) {
          const tracesResponse = await api.listTraces(1, 1, project.id);
          if (tracesResponse.data && tracesResponse.data.total > 0) {
            results.projectsWithTraces.push({
              workspaceName: config.workspaceName,
              projectId: project.id,
              projectName: project.name,
              traceCount: tracesResponse.data.total,
            });
          }
        }
      }
    } else {
      // If no workspace is defined, try to discover available workspaces
      console.log('No predefined workspace, attempting to discover workspaces...');

      // This endpoint may not exist, but we can try
      const workspacesResponse = await api.listWorkspaces();
      if (workspacesResponse.data) {
        results.availableWorkspaces = workspacesResponse.data;

        // Try each workspace
        for (const workspace of results.availableWorkspaces) {
          // Temporarily set workspace for API calls
          const originalWorkspace = config.workspaceName;
          config.workspaceName = workspace.name;

          // List projects in this workspace
          const projectsResponse = await api.listProjects();
          if (projectsResponse.data && projectsResponse.data.content) {
            // Check each project for traces
            for (const project of projectsResponse.data.content) {
              const tracesResponse = await api.listTraces(1, 1, project.id);
              if (tracesResponse.data && tracesResponse.data.total > 0) {
                results.projectsWithTraces.push({
                  workspaceName: workspace.name,
                  projectId: project.id,
                  projectName: project.name,
                  traceCount: tracesResponse.data.total,
                });
              }
            }
          }

          // Restore original workspace setting
          config.workspaceName = originalWorkspace;
        }
      } else {
        // Try with "default" workspace
        const originalWorkspace = config.workspaceName;
        config.workspaceName = 'default';

        // List projects in default workspace
        const projectsResponse = await api.listProjects();
        if (projectsResponse.data && projectsResponse.data.content) {
          // Check each project for traces
          for (const project of projectsResponse.data.content) {
            const tracesResponse = await api.listTraces(1, 1, project.id);
            if (tracesResponse.data && tracesResponse.data.total > 0) {
              results.projectsWithTraces.push({
                workspaceName: 'default',
                projectId: project.id,
                projectName: project.name,
                traceCount: tracesResponse.data.total,
              });
            }
          }
        }

        // Restore original workspace setting
        config.workspaceName = originalWorkspace;
      }
    }
  } catch (error) {
    console.error('Error finding workspaces and projects:', error);
  }

  return results;
}

/**
 * Run tests for all API endpoints
 */
async function runApiTests() {
  console.log('🔍 Testing Opik API with the following configuration:');
  console.log(`- API Base URL: ${config.apiBaseUrl}`);
  console.log(`- Self-hosted: ${config.isSelfHosted ? 'Yes' : 'No'}`);
  console.log(`- Workspace: ${config.workspaceName || 'None'}`);

  // Set debug mode for this run
  config.debugMode = true;
  console.log(`- Debug mode: ${config.debugMode ? 'Enabled' : 'Disabled'}`);
  console.log('\n');

  try {
    // Find workspaces and projects with traces
    console.log('🔎 FINDING WORKSPACES AND PROJECTS WITH TRACES');
    const discovery = await findWorkspacesAndProjects();

    if (discovery.projectsWithTraces.length > 0) {
      console.log(`\nFound ${discovery.projectsWithTraces.length} projects with traces:`);
      discovery.projectsWithTraces.forEach((project, index) => {
        console.log(
          `${index + 1}. Workspace: ${project.workspaceName}, Project: ${project.projectName} (${project.projectId}), Traces: ${project.traceCount}`
        );
      });

      // Look for the 'Therapist Chat' project first
      let testProject = discovery.projectsWithTraces.find(p => p.projectName === 'Therapist Chat');

      // If not found, use the first project with traces
      if (!testProject) {
        testProject = discovery.projectsWithTraces[0];
      }

      console.log(
        `\nUsing project "${testProject.projectName}" in workspace "${testProject.workspaceName}" for testing`
      );

      // Set the workspace for testing
      const originalWorkspace = config.workspaceName;
      config.workspaceName = testProject.workspaceName;

      // Test basic connection first
      console.log('\n🔌 TESTING CONNECTION');
      const connectionTest = await api.testConnection();
      if (connectionTest.data) {
        console.log('Connection successful');
        if (connectionTest.data.total) {
          console.log(`Found ${connectionTest.data.total} projects`);
        }
        console.log(JSON.stringify(connectionTest.data, null, 2));
      } else {
        console.log(`Connection failed: ${connectionTest.error}`);
      }
      console.log('\n');

      // Continue with other tests only if connection was successful
      if (connectionTest.error) {
        console.error('Cannot continue tests due to connection issues');
        return;
      }

      // Test traces for the selected project
      console.log('\n🔍 TESTING TRACES API');
      console.log(`Using project ID: ${testProject.projectId} for traces`);
      const tracesResponse = await api.listTraces(1, 10, testProject.projectId);

      if (tracesResponse.data) {
        console.log(`Found ${tracesResponse.data.total} traces`);
        console.log(JSON.stringify(tracesResponse.data, null, 2));

        // If there are traces, get details for the first one
        if (tracesResponse.data.content && tracesResponse.data.content.length > 0) {
          const traceId = tracesResponse.data.content[0].id;
          console.log(`\nGetting details for trace: ${traceId}`);

          const traceDetail = await api.getTrace(traceId);
          console.log(JSON.stringify(traceDetail.data, null, 2));
        }
      } else {
        console.error('Error fetching traces:', tracesResponse.error);
      }

      // Restore original workspace setting
      config.workspaceName = originalWorkspace;
    } else {
      console.log('\nNo projects with traces found. Continuing with general tests...');

      // Default test flow...
      // Test Projects API
      console.log('📁 TESTING PROJECTS API');
      const projectsResponse = await api.listProjects();
      let firstProjectId = null;

      if (projectsResponse.data) {
        console.log(`Found ${projectsResponse.data.total} projects`);
        console.log(JSON.stringify(projectsResponse.data, null, 2));

        // If there are projects, get details for the first one
        if (projectsResponse.data.content && projectsResponse.data.content.length > 0) {
          firstProjectId = projectsResponse.data.content[0].id;
          console.log(`\nGetting details for project: ${firstProjectId}`);

          const projectDetail = await api.getProject(firstProjectId);
          console.log(JSON.stringify(projectDetail.data, null, 2));
        }
      } else {
        console.error('Error fetching projects:', projectsResponse.error);
      }

      // Test Trace Stats API
      console.log('\n📊 TESTING TRACE STATS API');
      if (firstProjectId) {
        console.log(`Using project ID: ${firstProjectId} for trace stats`);
        const traceStatsResponse = await api.getTraceStats(firstProjectId);

        if (traceStatsResponse.data) {
          console.log('Trace statistics:');
          console.log(JSON.stringify(traceStatsResponse.data, null, 2));
        } else {
          console.error('Error fetching trace stats:', traceStatsResponse.error);
        }
      } else {
        console.log('No projects available to test trace stats API');
      }

      // Test Prompts API
      console.log('\n📝 TESTING PROMPTS API');
      const promptsResponse = await api.listPrompts();
      if (promptsResponse.data) {
        console.log(`Found ${promptsResponse.data.total} prompts`);
        console.log(JSON.stringify(promptsResponse.data, null, 2));
      } else {
        console.error('Error fetching prompts:', promptsResponse.error);
      }

      // Test Metrics API
      console.log('\n📈 TESTING METRICS API');
      const metricsResponse = await api.getMetrics();
      if (metricsResponse.data) {
        console.log('Metrics:');
        console.log(JSON.stringify(metricsResponse.data, null, 2));
      } else {
        console.error('Error fetching metrics:', metricsResponse.error);
      }

      // Test with specific 'Therapist Chat' project
      console.log('\n🧠 TESTING WITH THERAPIST CHAT PROJECT');
      const therapistChatProjectId = '0194fdd8-de46-73c4-b0ac-381cec5fbf5c';

      // Get project details
      console.log(`\nGetting details for Therapist Chat project: ${therapistChatProjectId}`);
      const therapistChatProject = await api.getProject(therapistChatProjectId);
      if (therapistChatProject.data) {
        console.log(JSON.stringify(therapistChatProject.data, null, 2));

        // Get traces for this project
        console.log('\nGetting traces for Therapist Chat project:');
        const therapistChatTraces = await api.listTraces(1, 10, therapistChatProjectId);
        if (therapistChatTraces.data) {
          console.log(`Found ${therapistChatTraces.data.total} traces`);
          console.log(JSON.stringify(therapistChatTraces.data, null, 2));

          // Get details for first trace if available
          if (therapistChatTraces.data.content && therapistChatTraces.data.content.length > 0) {
            const traceId = therapistChatTraces.data.content[0].id;
            console.log(`\nGetting details for trace: ${traceId}`);

            const traceDetail = await api.getTrace(traceId);
            console.log(JSON.stringify(traceDetail.data, null, 2));
          }
        } else {
          console.error('Error fetching Therapist Chat traces:', therapistChatTraces.error);
        }
      } else {
        console.error('Error fetching Therapist Chat project:', therapistChatProject.error);
      }
    }
  } catch (err) {
    console.error('Error running API tests:', err);
  }
}

async function main() {
  console.log('Starting MCP test client...');

  // Create a transport that runs our server
  const transport = new StdioClientTransport({
    command: 'node',
    args: ['build/index.js', '--debug', 'true'],
  });

  // Add event handlers for lifecycle events
  transport.onerror = (error: Error) => {
    console.error('Transport error:', error);
  };

  transport.onclose = () => {
    console.log('Transport connection closed');
  };

  // Create the client
  const client = new Client(
    {
      name: 'test-client',
      version: '2.0.0',
    },
    {
      capabilities: {
        tools: {}, // We're interested in tools
      },
    }
  );

  try {
    // Connect to the server
    console.log('Connecting to MCP server...');
    await client.connect(transport);
    console.log('Connected successfully!');

    // List available tools
    console.log('Requesting tool list...');
    const tools = await client.listTools();

    console.log('Available tools:');
    console.log(JSON.stringify(tools, null, 2));

    // Close the connection
    await client.close();
    console.log('Connection closed.');
  } catch (error) {
    console.error('Error:', error);
  }
}

// ESM-compatible entry point detection
const isMainModule = import.meta.url === `file://${process.argv[1]}`;

// Run the tests when this file is executed directly
if (isMainModule) {
  runApiTests().then(() => {
    console.log('\n✅ API tests completed');
  });
}

main().catch(error => {
  console.error('Fatal error:', error);
  process.exit(1);
});

export { api, makeApiRequest };

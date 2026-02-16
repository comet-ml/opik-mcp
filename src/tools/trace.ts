import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { logToFile } from '../utils/logging.js';
import {
  ProjectResponse,
  TraceResponse,
  SingleTraceResponse,
  TraceStatsResponse,
} from './../types.js';

export const loadTraceTools = (server: any) => {
  server.tool(
    'list-traces',
    'Get a list of traces from a project. Use this for basic trace retrieval and overview',
    {
      page: z.number().optional().default(1).describe('Page number for pagination (starts at 1)'),
      size: z
        .number()
        .optional()
        .default(10)
        .describe('Number of traces per page (1-100, default 10)'),
      projectId: z
        .string()
        .optional()
        .describe(
          'Project ID to filter traces. If not provided, will use the first available project'
        ),
      projectName: z
        .string()
        .optional()
        .describe(
          'Project name to filter traces (alternative to projectId). Example: "My AI Assistant"'
        ),
      workspaceName: z
        .string()
        .optional()
        .describe('Workspace name to use instead of the default workspace'),
    },
    async (args: any) => {
      const { page = 1, size = 10, projectId, projectName, workspaceName } = args;
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

        if (
          projectsResponse.data &&
          projectsResponse.data.content &&
          projectsResponse.data.content.length > 0
        ) {
          const firstProject = projectsResponse.data.content[0];
          url += `&project_id=${firstProject.id}`;
          logToFile(
            `No project specified, using first available: ${firstProject.name} (${firstProject.id})`
          );
        } else {
          return {
            content: [
              {
                type: 'text',
                text: 'Error: No project ID or name provided, and no projects found',
              },
            ],
          };
        }
      }

      const response = await makeApiRequest<TraceResponse>(url, {}, workspaceName);

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch traces' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Found ${response.data.total} traces (showing page ${
              response.data.page
            } of ${Math.ceil(response.data.total / response.data.size)})`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data.content, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'get-trace-by-id',
    'Get detailed information about a specific trace including input, output, metadata, and timing information',
    {
      traceId: z
        .string()
        .describe(
          'ID of the trace to fetch (UUID format, e.g. "123e4567-e89b-12d3-a456-426614174000")'
        ),
      workspaceName: z
        .string()
        .optional()
        .describe('Workspace name to use instead of the default workspace'),
    },
    async (args: any) => {
      const { traceId, workspaceName } = args;
      const response = await makeApiRequest<SingleTraceResponse>(
        `/v1/private/traces/${traceId}`,
        {},
        workspaceName
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch trace' }],
        };
      }

      // Format the response for better readability
      const formattedResponse: any = { ...response.data };

      // Format input/output if they're large
      if (
        formattedResponse.input &&
        typeof formattedResponse.input === 'object' &&
        Object.keys(formattedResponse.input).length > 0
      ) {
        formattedResponse.input = JSON.stringify(formattedResponse.input, null, 2);
      }

      if (
        formattedResponse.output &&
        typeof formattedResponse.output === 'object' &&
        Object.keys(formattedResponse.output).length > 0
      ) {
        formattedResponse.output = JSON.stringify(formattedResponse.output, null, 2);
      }

      return {
        content: [
          {
            type: 'text',
            text: `Trace Details for ID: ${traceId}`,
          },
          {
            type: 'text',
            text: JSON.stringify(formattedResponse, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'get-trace-stats',
    'Get aggregated statistics for traces including counts, costs, token usage, and performance metrics over time',
    {
      projectId: z
        .string()
        .optional()
        .describe(
          'Project ID to filter traces. If not provided, will use the first available project'
        ),
      projectName: z
        .string()
        .optional()
        .describe('Project name to filter traces (alternative to projectId)'),
      startDate: z
        .string()
        .optional()
        .describe('Start date in ISO format (YYYY-MM-DD). Example: "2024-01-01"'),
      endDate: z
        .string()
        .optional()
        .describe('End date in ISO format (YYYY-MM-DD). Example: "2024-01-31"'),
      workspaceName: z
        .string()
        .optional()
        .describe('Workspace name to use instead of the default workspace'),
    },
    async (args: any) => {
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

        if (
          projectsResponse.data &&
          projectsResponse.data.content &&
          projectsResponse.data.content.length > 0
        ) {
          const firstProject = projectsResponse.data.content[0];
          queryParams.push(`project_id=${firstProject.id}`);
          logToFile(
            `No project specified, using first available: ${firstProject.name} (${firstProject.id})`
          );
        } else {
          return {
            content: [
              {
                type: 'text',
                text: 'Error: No project ID or name provided, and no projects found',
              },
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
          content: [{ type: 'text', text: response.error || 'Failed to fetch trace statistics' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Trace Statistics:`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'search-traces',
    'Advanced search for traces with complex filtering and query capabilities',
    {
      projectId: z.string().optional().describe('Project ID to search within'),
      projectName: z.string().optional().describe('Project name to search within'),
      query: z
        .string()
        .optional()
        .describe(
          'Text query to search in trace names, inputs, outputs, and metadata. Example: "error" or "user_query:hello"'
        ),
      filters: z
        .record(z.any())
        .optional()
        .describe(
          'Advanced filters as key-value pairs. Examples: {"status": "error"}, {"model": "gpt-4"}, {"duration_ms": {"$gt": 1000}}'
        ),
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(10).describe('Number of traces per page (max 100)'),
      sortBy: z
        .string()
        .optional()
        .describe('Field to sort by. Options: "created_at", "duration", "name", "status"'),
      sortOrder: z
        .enum(['asc', 'desc'])
        .optional()
        .default('desc')
        .describe('Sort order: ascending or descending'),
      workspaceName: z.string().optional().describe('Workspace name to use instead of the default'),
    },
    async (args: any) => {
      const {
        projectId,
        projectName,
        query,
        filters,
        page,
        size,
        sortBy,
        sortOrder,
        workspaceName,
      } = args;

      // Build search request body
      const searchBody: any = {
        page: page || 1,
        size: size || 10,
      };

      // Add project filtering
      if (projectId) {
        searchBody.project_id = projectId;
      } else if (projectName) {
        searchBody.project_name = projectName;
      } else {
        // If no project specified, we need to find one for the API to work
        const projectsResponse = await makeApiRequest<ProjectResponse>(
          `/v1/private/projects?page=1&size=1`,
          {},
          workspaceName
        );

        if (
          projectsResponse.data &&
          projectsResponse.data.content &&
          projectsResponse.data.content.length > 0
        ) {
          const firstProject = projectsResponse.data.content[0];
          searchBody.project_id = firstProject.id;
          logToFile(
            `No project specified for search, using first available: ${firstProject.name} (${firstProject.id})`
          );
        } else {
          return {
            content: [
              {
                type: 'text',
                text: 'Error: No project ID or name provided, and no projects found',
              },
            ],
          };
        }
      }

      // Add query if provided
      if (query) {
        searchBody.query = query;
      }

      // Add filters if provided
      if (filters) {
        searchBody.filters = filters;
      }

      // Add sorting if provided
      if (sortBy) {
        searchBody.sort_by = sortBy;
        if (sortOrder) {
          searchBody.sort_order = sortOrder;
        }
      }

      const response = await makeApiRequest<TraceResponse>(
        '/v1/private/traces/search',
        {
          method: 'POST',
          body: JSON.stringify(searchBody),
        },
        workspaceName
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to search traces' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Search found ${response.data.total} traces (page ${response.data.page} of ${Math.ceil(response.data.total / response.data.size)})`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data.content, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'get-trace-threads',
    'Get trace threads (conversation groupings) to view related traces that belong to the same conversation or session',
    {
      projectId: z.string().optional().describe('Project ID to filter threads'),
      projectName: z.string().optional().describe('Project name to filter threads'),
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(10).describe('Number of threads per page'),
      threadId: z
        .string()
        .optional()
        .describe(
          'Specific thread ID to retrieve (useful for getting all traces in a conversation)'
        ),
      workspaceName: z.string().optional().describe('Workspace name to use instead of the default'),
    },
    async (args: any) => {
      const { projectId, projectName, page, size, threadId, workspaceName } = args;
      let resolvedProjectId = projectId;
      let resolvedProjectName = projectName;

      if (!resolvedProjectId && !resolvedProjectName) {
        // If no project specified, we need to find one for the API to work
        const projectsResponse = await makeApiRequest<ProjectResponse>(
          `/v1/private/projects?page=1&size=1`,
          {},
          workspaceName
        );

        if (
          projectsResponse.data &&
          projectsResponse.data.content &&
          projectsResponse.data.content.length > 0
        ) {
          const firstProject = projectsResponse.data.content[0];
          resolvedProjectId = firstProject.id;
          logToFile(
            `No project specified for threads, using first available: ${firstProject.name} (${firstProject.id})`
          );
        } else {
          return {
            content: [
              {
                type: 'text',
                text: 'Error: No project ID or name provided, and no projects found',
              },
            ],
          };
        }
      }

      let response;
      if (threadId) {
        const requestBody: Record<string, string> = {
          thread_id: threadId,
        };

        if (resolvedProjectId) {
          requestBody.project_id = resolvedProjectId;
        } else if (resolvedProjectName) {
          requestBody.project_name = resolvedProjectName;
        }

        response = await makeApiRequest<any>(
          '/v1/private/traces/threads/retrieve',
          {
            method: 'POST',
            body: JSON.stringify(requestBody),
          },
          workspaceName
        );
      } else {
        let url = `/v1/private/traces/threads?page=${page || 1}&size=${size || 10}`;
        if (resolvedProjectId) {
          url += `&project_id=${encodeURIComponent(resolvedProjectId)}`;
        } else if (resolvedProjectName) {
          url += `&project_name=${encodeURIComponent(resolvedProjectName)}`;
        }

        response = await makeApiRequest<any>(url, {}, workspaceName);
      }

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch trace threads' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: threadId
              ? `Thread details for ID: ${threadId}`
              : `Found ${response.data.total || response.data.length || 0} trace threads`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'add-trace-feedback',
    'Add feedback scores to a trace for quality evaluation and monitoring. Useful for rating trace quality, relevance, or custom metrics',
    {
      traceId: z.string().describe('ID of the trace to add feedback to'),
      scores: z
        .array(
          z.object({
            name: z
              .string()
              .describe(
                'Name of the feedback metric (e.g., "relevance", "accuracy", "helpfulness", "quality")'
              ),
            value: z
              .number()
              .describe('Score value. Commonly 0.0-1.0, but custom numeric scales are supported.'),
            reason: z.string().optional().describe('Optional explanation for the score'),
            source: z
              .enum(['ui', 'sdk', 'online_scoring'])
              .optional()
              .default('sdk')
              .describe('Feedback source, defaults to "sdk"'),
            categoryName: z
              .string()
              .optional()
              .describe('Optional category name for grouped feedback dimensions'),
          })
        )
        .describe(
          'Array of feedback scores to add. Each score should have a metric name and numeric value'
        ),
      workspaceName: z.string().optional().describe('Workspace name to use instead of the default'),
    },
    async (args: any) => {
      const { traceId, scores, workspaceName } = args;

      // Validate scores format
      if (!scores || !Array.isArray(scores) || scores.length === 0) {
        return {
          content: [
            {
              type: 'text',
              text: 'Error: At least one feedback score is required. Format: [{"name": "relevance", "value": 0.8}]',
            },
          ],
        };
      }

      // Transform scores to the expected API format
      const feedbackScores = scores.map((score: any) => ({
        name: score.name,
        value: score.value,
        source: score.source || 'sdk',
        ...(score.categoryName && { category_name: score.categoryName }),
        ...(score.reason && { reason: score.reason }),
      }));

      // Prefer current Opik API behavior: one score per request on trace-specific endpoint.
      let modernApiError: string | null = null;
      for (const score of feedbackScores) {
        const response = await makeApiRequest<any>(
          `/v1/private/traces/${traceId}/feedback-scores`,
          {
            method: 'PUT',
            body: JSON.stringify(score),
          },
          workspaceName
        );

        if (response.error) {
          modernApiError = response.error;
          break;
        }
      }

      if (modernApiError) {
        // Backward compatibility for older self-hosted deployments that expect batch payloads.
        const legacyResponse = await makeApiRequest<any>(
          `/v1/private/traces/${traceId}/feedback-scores`,
          {
            method: 'PUT',
            body: JSON.stringify({ scores: feedbackScores }),
          },
          workspaceName
        );

        if (legacyResponse.error) {
          return {
            content: [
              {
                type: 'text',
                text: `Error adding feedback (modern API failed: ${modernApiError}; legacy fallback failed: ${legacyResponse.error})`,
              },
            ],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: `Successfully added ${scores.length} feedback score(s) to trace ${traceId} using legacy fallback`,
            },
            {
              type: 'text',
              text: `Added scores: ${scores.map((s: any) => `${s.name}: ${s.value}`).join(', ')}`,
            },
          ],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Successfully added ${scores.length} feedback score(s) to trace ${traceId}`,
          },
          {
            type: 'text',
            text: `Added scores: ${scores.map((s: any) => `${s.name}: ${s.value}`).join(', ')}`,
          },
        ],
      };
    }
  );

  return server;
};

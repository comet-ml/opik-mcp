import { z } from 'zod';
import {
  buildTraceFilters,
  callSdk,
  getOpikApi,
  getRequestOptions,
  resolveProjectIdentifier,
} from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';

export const loadTraceTools = (server: any) => {
  registerTool(
    server,
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

      const resolved = await resolveProjectIdentifier(projectId, projectName, workspaceName);
      if (resolved.error) {
        return {
          content: [{ type: 'text', text: `Error: ${resolved.error}` }],
        };
      }

      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.traces.getTracesByProject(
          {
            page,
            size,
            ...(resolved.projectId && { projectId: resolved.projectId }),
            ...(resolved.projectName && { projectName: resolved.projectName }),
          },
          getRequestOptions(workspaceName)
        )
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch traces' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Found ${response.data.total} traces (showing page ${response.data.page} of ${Math.ceil(response.data.total / response.data.size)})`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data.content, null, 2),
          },
        ],
      };
    }
  );

  registerTool(
    server,
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
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.traces.getTraceById(traceId, getRequestOptions(workspaceName))
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch trace' }],
        };
      }

      const formattedResponse: any = { ...response.data };

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

  registerTool(
    server,
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

      const resolved = await resolveProjectIdentifier(projectId, projectName, workspaceName);
      if (resolved.error) {
        return {
          content: [{ type: 'text', text: `Error: ${resolved.error}` }],
        };
      }

      const filters = buildTraceFilters(undefined, undefined, startDate, endDate);
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.traces.getTraceStats(
          {
            ...(resolved.projectId && { projectId: resolved.projectId }),
            ...(resolved.projectName && { projectName: resolved.projectName }),
            ...(filters && { filters }),
          },
          getRequestOptions(workspaceName)
        )
      );

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

  registerTool(
    server,
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

      const resolved = await resolveProjectIdentifier(projectId, projectName, workspaceName);
      if (resolved.error) {
        return {
          content: [{ type: 'text', text: `Error: ${resolved.error}` }],
        };
      }

      const sdkFilters = buildTraceFilters(query, filters);
      const sorting = sortBy ? `${sortBy}:${sortOrder || 'desc'}` : undefined;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.traces.getTracesByProject(
          {
            page: page || 1,
            size: size || 10,
            ...(resolved.projectId && { projectId: resolved.projectId }),
            ...(resolved.projectName && { projectName: resolved.projectName }),
            ...(sdkFilters && { filters: sdkFilters }),
            ...(sorting && { sorting }),
          },
          getRequestOptions(workspaceName)
        )
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

  registerTool(
    server,
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

      const resolved = await resolveProjectIdentifier(projectId, projectName, workspaceName);
      if (resolved.error) {
        return {
          content: [{ type: 'text', text: `Error: ${resolved.error}` }],
        };
      }

      const api = getOpikApi();
      const response = threadId
        ? await callSdk<any>(() =>
            api.traces.getTraceThread(
              {
                threadId,
                ...(resolved.projectId && { projectId: resolved.projectId }),
                ...(resolved.projectName && { projectName: resolved.projectName }),
              },
              getRequestOptions(workspaceName)
            )
          )
        : await callSdk<any>(() =>
            api.traces.getTraceThreads(
              {
                page: page || 1,
                size: size || 10,
                ...(resolved.projectId && { projectId: resolved.projectId }),
                ...(resolved.projectName && { projectName: resolved.projectName }),
              },
              getRequestOptions(workspaceName)
            )
          );

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

  registerTool(
    server,
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

      const api = getOpikApi();
      for (const score of scores) {
        const response = await callSdk<any>(() =>
          api.traces.addTraceFeedbackScore(
            traceId,
            {
              name: score.name,
              value: score.value,
              source: score.source || 'sdk',
              ...(score.reason && { reason: score.reason }),
              ...(score.categoryName && { categoryName: score.categoryName }),
            },
            getRequestOptions(workspaceName)
          )
        );

        if (response.error) {
          return {
            content: [
              {
                type: 'text',
                text: `Error adding feedback: ${response.error}`,
              },
            ],
          };
        }
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

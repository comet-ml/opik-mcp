import { z } from 'zod';
import {
  buildTraceFilters,
  callSdk,
  getOpikApi,
  getRequestOptions,
  resolveProjectIdentifier,
} from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';
import { isoDateSchema, pageSchema, sizeSchema, workspaceNameSchema } from './schema.js';

interface TraceToolOptions {
  includeCoreTools?: boolean;
  includeExpertActions?: boolean;
}

export const loadTraceTools = (server: any, options: TraceToolOptions = {}) => {
  const { includeCoreTools = true, includeExpertActions = true } = options;

  if (includeCoreTools) {
    registerTool(
      server,
      'list-traces',
      'List traces for a project for quick inspection and navigation.',
      {
        page: pageSchema,
        size: sizeSchema(10),
        projectId: z
          .string()
          .optional()
          .describe('Optional project ID. If omitted, the first available project is used.'),
        projectName: z
          .string()
          .optional()
          .describe('Optional project name (alternative to projectId).'),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'List Traces',
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      }
    );

    registerTool(
      server,
      'get-trace-by-id',
      'Get full details for a trace, including metadata and serialized input/output.',
      {
        traceId: z.string().min(1).describe('Trace ID.'),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'Get Trace By ID',
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      }
    );

    registerTool(
      server,
      'get-trace-stats',
      'Get aggregated trace statistics (count, tokens, cost, and duration) over time.',
      {
        projectId: z
          .string()
          .optional()
          .describe('Optional project ID. If omitted, the first available project is used.'),
        projectName: z
          .string()
          .optional()
          .describe('Optional project name (alternative to projectId).'),
        startDate: isoDateSchema,
        endDate: isoDateSchema,
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'Get Trace Stats',
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      }
    );

    registerTool(
      server,
      'get-trace-threads',
      'List trace threads (conversation/session groupings) or fetch one thread by ID.',
      {
        projectId: z.string().optional().describe('Optional project ID filter.'),
        projectName: z.string().optional().describe('Optional project name filter.'),
        page: pageSchema,
        size: sizeSchema(10),
        threadId: z
          .string()
          .optional()
          .describe(
            'Optional thread ID. When set, returns that thread instead of paginated listing.'
          ),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'Get Trace Threads',
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      }
    );
  }

  if (includeExpertActions) {
    registerTool(
      server,
      'search-traces',
      'Search traces with optional text query, structured filters, and sorting.',
      {
        projectId: z.string().optional().describe('Optional project ID to constrain search.'),
        projectName: z.string().optional().describe('Optional project name to constrain search.'),
        query: z
          .string()
          .optional()
          .describe('Optional free-text query across trace name/input/output/metadata.'),
        filters: z
          .record(z.any())
          .optional()
          .describe(
            'Optional advanced filters, e.g. {"status":"error"} or {"duration_ms":{"$gt":1000}}.'
          ),
        page: pageSchema,
        size: sizeSchema(10),
        sortBy: z
          .enum(['created_at', 'duration', 'name', 'status'])
          .optional()
          .describe('Optional sort field.'),
        sortOrder: z.enum(['asc', 'desc']).optional().default('desc').describe('Sort direction.'),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'Search Traces',
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      }
    );

    registerTool(
      server,
      'add-trace-feedback',
      'Attach one or more feedback scores to a trace.',
      {
        traceId: z.string().min(1).describe('Target trace ID.'),
        scores: z
          .array(
            z.object({
              name: z
                .string()
                .min(1)
                .describe('Feedback metric name, e.g. relevance, accuracy, helpfulness.'),
              value: z.number().finite().describe('Numeric score value.'),
              reason: z.string().optional().describe('Optional reason for this score.'),
              source: z
                .enum(['ui', 'sdk', 'online_scoring'])
                .optional()
                .default('sdk')
                .describe('Feedback source.'),
              categoryName: z
                .string()
                .optional()
                .describe('Optional category for grouped feedback dimensions.'),
            })
          )
          .min(1)
          .describe('One or more feedback score objects.'),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'Add Trace Feedback',
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      }
    );
  }

  return server;
};

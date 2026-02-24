import { z } from 'zod';
import {
  callSdk,
  getOpikApi,
  getRequestOptions,
  mapMetricType,
  resolveProjectIdentifier,
} from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';
import { isoDateSchema, workspaceNameSchema } from './schema.js';

export const loadMetricTools = (server: any) => {
  registerTool(
    server,
    'get-metrics',
    'Get project metrics for a date range and optional metric type.',
    {
      metricName: z
        .string()
        .optional()
        .describe(
          'Optional metric type/alias (TRACE_COUNT, TOKEN_USAGE, COST, DURATION, FEEDBACK).'
        ),
      projectId: z.string().optional().describe('Optional project ID.'),
      projectName: z
        .string()
        .optional()
        .describe('Optional project name (alternative to projectId).'),
      startDate: isoDateSchema,
      endDate: isoDateSchema,
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { metricName, projectId, projectName, startDate, endDate, workspaceName } = args;

      const resolved = await resolveProjectIdentifier(projectId, projectName, workspaceName);
      if (resolved.error || (!resolved.projectId && !resolved.projectName)) {
        return {
          content: [
            {
              type: 'text',
              text: `Error: ${resolved.error || 'No project available for metrics query'}`,
            },
          ],
        };
      }

      if (!resolved.projectId) {
        return {
          content: [
            {
              type: 'text',
              text: 'Error: Metrics queries require a resolvable project ID',
            },
          ],
        };
      }

      const metricType = mapMetricType(metricName);
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.projects.getProjectMetrics(
          resolved.projectId as string,
          {
            ...(metricType && { metricType }),
            interval: 'DAILY',
            ...(startDate && { intervalStart: new Date(startDate) }),
            ...(endDate && { intervalEnd: new Date(endDate) }),
          },
          getRequestOptions(workspaceName)
        )
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch metrics' }],
        };
      }

      const metricWarning =
        metricName && !metricType
          ? `\nNote: metricName "${metricName}" is not a known metric type in the SDK and was ignored.`
          : '';

      return {
        content: [
          {
            type: 'text',
            text: `Metrics for project ${resolved.projectId}${metricWarning}`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  return server;
};

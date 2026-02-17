import { z } from 'zod';
import type { OpikConfig } from '../config.js';
import {
  getCapabilitiesDescription,
  getEnabledCapabilities,
  opikCapabilities,
} from '../utils/capabilities.js';
import { getAllExampleTasks, getExampleForTask } from '../utils/examples.js';
import { getAllMetricsInfo, getMetricInfo } from '../utils/metrics-info.js';
import { getTracingInfo } from '../utils/tracing-info.js';
import { registerTool } from './registration.js';

function formatTopicHelp(topic: keyof typeof opikCapabilities): string {
  if (topic === 'general') {
    const general = opikCapabilities.general;
    return [
      '# General API Information',
      '',
      `- API Version: ${general.apiVersion}`,
      `- Authentication: ${general.authentication}`,
      `- Rate Limit: ${general.rateLimit}`,
      `- Supported Formats: ${general.supportedFormats.join(', ')}`,
    ].join('\n');
  }

  const section = opikCapabilities[topic] as {
    available: boolean;
    features: string[];
    limitations: string[];
  };

  return [
    `# ${topic.charAt(0).toUpperCase()}${topic.slice(1)}`,
    '',
    `Available: ${section.available ? 'Yes' : 'No'}`,
    '',
    '## Features',
    ...section.features.map(feature => `- ${feature}`),
    '',
    '## Limitations',
    ...section.limitations.map(limitation => `- ${limitation}`),
  ].join('\n');
}

export const loadCapabilitiesTools = (server: any, config: OpikConfig) => {
  registerTool(
    server,
    'get-server-info',
    'Return server configuration and enabled Opik capabilities.',
    {},
    async (_args: any) => {
      const serverInfo = {
        apiBaseUrl: config.apiBaseUrl,
        isSelfHosted: config.isSelfHosted,
        workspaceName: config.workspaceName,
        transport: config.transport,
        mcpName: config.mcpName,
        mcpVersion: config.mcpVersion,
        mcpDefaultWorkspace: config.mcpDefaultWorkspace,
        enabledToolsets: config.enabledToolsets,
        hasApiKey: Boolean(config.apiKey),
        capabilities: getEnabledCapabilities(config),
      };

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(serverInfo, null, 2),
          },
        ],
      };
    },
    { requiresApiKey: false }
  );

  registerTool(
    server,
    'get-opik-help',
    'Return Opik capability documentation, optionally filtered by topic.',
    {
      topic: z
        .enum(['prompts', 'projects', 'traces', 'metrics', 'general'])
        .optional()
        .describe('Optional capability topic to describe'),
    },
    async (args: { topic?: 'prompts' | 'projects' | 'traces' | 'metrics' | 'general' }) => {
      if (args.topic) {
        return {
          content: [
            {
              type: 'text',
              text: formatTopicHelp(args.topic),
            },
          ],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: getCapabilitiesDescription(config),
          },
        ],
      };
    },
    { requiresApiKey: false }
  );

  registerTool(
    server,
    'get-opik-examples',
    'Return Opik usage examples for a requested task.',
    {
      task: z
        .string()
        .optional()
        .describe('Optional task name, e.g. "create prompt", "log trace", "evaluate response"'),
    },
    async (args: { task?: string }) => {
      const tasks = getAllExampleTasks();
      const example = getExampleForTask(args.task);

      if (!example) {
        return {
          content: [
            {
              type: 'text',
              text: args.task
                ? `No specific example found for task: ${args.task}. Available tasks: ${tasks.join(', ')}`
                : `Available tasks: ${tasks.join(', ')}`,
            },
          ],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: [
              `# Example: ${example.title}`,
              '',
              '## Description',
              example.description,
              '',
              '## Steps',
              ...example.steps.map((step, index) => `${index + 1}. ${step}`),
              '',
              '## Code Example',
              example.codeExample.trim(),
            ].join('\n'),
          },
        ],
      };
    },
    { requiresApiKey: false }
  );

  registerTool(
    server,
    'get-opik-metrics-info',
    'Return Opik metric definitions and usage guidance.',
    {
      metric: z
        .string()
        .optional()
        .describe('Optional metric name (e.g. hallucination, answerrelevance, moderation)'),
    },
    async (args: { metric?: string }) => {
      if (args.metric) {
        const metric = getMetricInfo(args.metric);

        if (!metric) {
          return {
            content: [
              {
                type: 'text',
                text: `Metric not found: ${args.metric}`,
              },
            ],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(metric, null, 2),
            },
          ],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(getAllMetricsInfo(), null, 2),
          },
        ],
      };
    },
    { requiresApiKey: false }
  );

  registerTool(
    server,
    'get-opik-tracing-info',
    'Return tracing guidance for traces, spans, feedback, search, and visualization.',
    {
      topic: z
        .enum(['traces', 'spans', 'feedback', 'search', 'visualization'])
        .optional()
        .describe('Optional tracing topic'),
    },
    async (args: { topic?: 'traces' | 'spans' | 'feedback' | 'search' | 'visualization' }) => {
      const info = getTracingInfo(args.topic);

      if (!info) {
        return {
          content: [
            {
              type: 'text',
              text: `No tracing info found for topic: ${args.topic}`,
            },
          ],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(info, null, 2),
          },
        ],
      };
    },
    { requiresApiKey: false }
  );

  return server;
};

import { z } from 'zod';
import { registerPrompt } from '../tools/registration.js';

export function loadCorePrompts(server: any) {
  registerPrompt(
    server,
    'opik-triage-workflow',
    'Guide for selecting the right Opik MCP toolset and first actions.',
    {
      goal: z.string().describe('User goal to accomplish with Opik.'),
      scope: z
        .enum(['core', 'prompts', 'datasets', 'traces', 'projects', 'metrics'])
        .default('core')
        .describe('Primary domain for this task.'),
    },
    async ({ goal, scope }: { goal: string; scope: string }) => ({
      messages: [
        {
          role: 'user',
          content: {
            type: 'text',
            text: [
              'You are operating the Opik MCP server.',
              `Goal: ${goal}`,
              `Scope: ${scope}`,
              'First, call get-server-info.',
              'Then propose a short 3-step action plan and execute only read operations before mutations.',
              'When mutating, confirm target IDs from read results.',
            ].join('\n'),
          },
        },
      ],
    }),
    { title: 'Opik Workflow Triage' }
  );

  registerPrompt(
    server,
    'opik-dataset-maintenance',
    'Workflow template for dataset curation and quality checks.',
    {
      datasetName: z.string().describe('Dataset name to inspect or curate.'),
      objective: z
        .string()
        .default('Find low-quality items and produce cleanup actions.')
        .describe('Dataset maintenance objective.'),
    },
    async ({ datasetName, objective }: { datasetName: string; objective: string }) => ({
      messages: [
        {
          role: 'user',
          content: {
            type: 'text',
            text: [
              `Dataset: ${datasetName}`,
              `Objective: ${objective}`,
              'Use list-datasets to locate the dataset ID.',
              'Use list-dataset-items with pagination to inspect records.',
              'Return a concise cleanup plan with explicit item IDs before delete-dataset-item.',
            ].join('\n'),
          },
        },
      ],
    }),
    { title: 'Dataset Maintenance' }
  );

  return server;
}

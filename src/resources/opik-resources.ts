import type { OpikConfig } from '../config.js';
import type { OpikToolset } from '../config.js';
import { callSdk, getOpikApi, getRequestOptions } from '../utils/opik-sdk.js';
import { registerResource, registerResourceTemplate } from '../tools/registration.js';

const DEFAULT_PAGE = 1;
const DEFAULT_SIZE = 10;
const MAX_SIZE = 100;

function parsePositiveInt(
  value: string | undefined,
  fallback: number,
  max: number = MAX_SIZE
): number {
  const parsed = Number.parseInt(value || '', 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return fallback;
  }
  return Math.min(parsed, max);
}

function toReadError(uri: string, message: string) {
  return {
    contents: [
      {
        uri,
        text: JSON.stringify({ error: message }, null, 2),
      },
    ],
  };
}

function includeToolset(enabled: Set<OpikToolset>, toolset: OpikToolset): boolean {
  return enabled.has(toolset);
}

export function loadOpikResources(server: any, config: OpikConfig): any {
  const enabledToolsets = new Set(config.enabledToolsets);
  const hasTraceRead =
    includeToolset(enabledToolsets, 'core') ||
    includeToolset(enabledToolsets, 'expert-trace-actions');
  const hasPromptRead = includeToolset(enabledToolsets, 'expert-prompts');
  const hasDatasetRead = includeToolset(enabledToolsets, 'expert-datasets');
  const hasProjectRead = includeToolset(enabledToolsets, 'core');

  registerResource(
    server,
    'workspace-info',
    'opik://workspace-info',
    'Workspace information for the configured Opik MCP server.',
    async () => ({
      contents: [
        {
          uri: 'opik://workspace-info',
          text: JSON.stringify(
            {
              name: config.workspaceName,
              apiUrl: config.apiBaseUrl,
              selfHosted: config.isSelfHosted,
              enabledToolsets: config.enabledToolsets,
            },
            null,
            2
          ),
        },
      ],
    })
  );

  if (hasProjectRead) {
    // Backward-compatible static URI while encouraging template usage for paging.
    registerResource(
      server,
      'projects-list',
      'opik://projects-list',
      'Project listing for the configured Opik workspace (first page only).',
      async () => {
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.projects.findProjects({ page: 1, size: 25 }));
        if (!response.data) {
          return toReadError('opik://projects-list', response.error || 'Failed to fetch projects');
        }
        return {
          contents: [
            {
              uri: 'opik://projects-list',
              text: JSON.stringify(response.data, null, 2),
            },
          ],
        };
      }
    );

    registerResourceTemplate(
      server,
      'projects-page',
      'opik://projects/{page}/{size}',
      'Paginated project listing by page and size.',
      async (uri: URL, variables: Record<string, string>) => {
        const page = parsePositiveInt(variables.page, DEFAULT_PAGE);
        const size = parsePositiveInt(variables.size, DEFAULT_SIZE);
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.projects.findProjects({ page, size }));
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch projects');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );
  }

  if (hasPromptRead) {
    registerResourceTemplate(
      server,
      'prompts-page',
      'opik://prompts/{page}/{size}',
      'Paginated prompt listing by page and size.',
      async (uri: URL, variables: Record<string, string>) => {
        const page = parsePositiveInt(variables.page, DEFAULT_PAGE);
        const size = parsePositiveInt(variables.size, DEFAULT_SIZE);
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.prompts.getPrompts({ page, size }));
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch prompts');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );

    registerResourceTemplate(
      server,
      'prompt-latest',
      'opik://prompt/{name}',
      'Latest prompt version by prompt name.',
      async (uri: URL, variables: Record<string, string>) => {
        const name = decodeURIComponent(variables.name || '');
        if (!name) {
          return toReadError(uri.toString(), 'Prompt name is required');
        }
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.prompts.retrievePromptVersion({ name }));
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch prompt');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );

    registerResourceTemplate(
      server,
      'prompt-commit',
      'opik://prompt/{name}/{commit}',
      'Specific prompt version by prompt name and commit.',
      async (uri: URL, variables: Record<string, string>) => {
        const name = decodeURIComponent(variables.name || '');
        const commit = decodeURIComponent(variables.commit || '');
        if (!name || !commit) {
          return toReadError(uri.toString(), 'Prompt name and commit are required');
        }
        const api = getOpikApi();
        const response = await callSdk<any>(() =>
          api.prompts.retrievePromptVersion({ name, commit })
        );
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch prompt version');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );
  }

  if (hasDatasetRead) {
    registerResourceTemplate(
      server,
      'datasets-page',
      'opik://datasets/{page}/{size}',
      'Paginated dataset listing by page and size.',
      async (uri: URL, variables: Record<string, string>) => {
        const page = parsePositiveInt(variables.page, DEFAULT_PAGE);
        const size = parsePositiveInt(variables.size, DEFAULT_SIZE);
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.datasets.findDatasets({ page, size }));
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch datasets');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );

    registerResourceTemplate(
      server,
      'dataset-by-id',
      'opik://dataset/{datasetId}',
      'Dataset details by dataset ID.',
      async (uri: URL, variables: Record<string, string>) => {
        const datasetId = decodeURIComponent(variables.datasetId || '');
        if (!datasetId) {
          return toReadError(uri.toString(), 'datasetId is required');
        }
        const api = getOpikApi();
        const response = await callSdk<any>(() => api.datasets.getDatasetById(datasetId));
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch dataset');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );

    registerResourceTemplate(
      server,
      'dataset-items-page',
      'opik://dataset/{datasetId}/items/{page}/{size}',
      'Paginated dataset items by dataset ID, page, and size.',
      async (uri: URL, variables: Record<string, string>) => {
        const datasetId = decodeURIComponent(variables.datasetId || '');
        const page = parsePositiveInt(variables.page, DEFAULT_PAGE);
        const size = parsePositiveInt(variables.size, 25, 500);
        if (!datasetId) {
          return toReadError(uri.toString(), 'datasetId is required');
        }
        const api = getOpikApi();
        const response = await callSdk<any>(() =>
          api.datasets.getDatasetItems(datasetId, { page, size })
        );
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch dataset items');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );
  }

  if (hasTraceRead) {
    registerResourceTemplate(
      server,
      'trace-by-id',
      'opik://trace/{traceId}',
      'Trace details by trace ID.',
      async (uri: URL, variables: Record<string, string>) => {
        const traceId = decodeURIComponent(variables.traceId || '');
        if (!traceId) {
          return toReadError(uri.toString(), 'traceId is required');
        }
        const api = getOpikApi();
        const response = await callSdk<any>(() =>
          api.traces.getTraceById(traceId, getRequestOptions())
        );
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch trace');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );

    registerResourceTemplate(
      server,
      'traces-by-project-page',
      'opik://traces/{projectId}/{page}/{size}',
      'Paginated traces by project ID, page, and size.',
      async (uri: URL, variables: Record<string, string>) => {
        const projectId = decodeURIComponent(variables.projectId || '');
        const page = parsePositiveInt(variables.page, DEFAULT_PAGE);
        const size = parsePositiveInt(variables.size, DEFAULT_SIZE);
        if (!projectId) {
          return toReadError(uri.toString(), 'projectId is required');
        }
        const api = getOpikApi();
        const response = await callSdk<any>(() =>
          api.traces.getTracesByProject({ projectId, page, size }, getRequestOptions())
        );
        if (!response.data) {
          return toReadError(uri.toString(), response.error || 'Failed to fetch traces');
        }
        return {
          contents: [{ uri: uri.toString(), text: JSON.stringify(response.data, null, 2) }],
        };
      }
    );
  }

  return server;
}

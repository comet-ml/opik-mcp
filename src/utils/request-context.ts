import { AsyncLocalStorage } from 'node:async_hooks';

export interface RequestContext {
  apiKey?: string;
  workspaceName?: string;
}

const requestContextStore = new AsyncLocalStorage<RequestContext>();

export function runWithRequestContext<T>(context: RequestContext, fn: () => T): T {
  return requestContextStore.run(context, fn);
}

export function getRequestContext(): RequestContext | undefined {
  return requestContextStore.getStore();
}

function normalizeHeaderValue(value: string | string[] | undefined): string | undefined {
  if (!value) {
    return undefined;
  }

  const raw = Array.isArray(value) ? value[0] : value;
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

export function extractContextFromHeaders(
  headers: Record<string, string | string[] | undefined>
): RequestContext {
  const authorization = normalizeHeaderValue(headers.authorization);
  const xApiKey = normalizeHeaderValue(headers['x-api-key']);
  const cometWorkspace = normalizeHeaderValue(headers['comet-workspace']);
  const xWorkspaceName = normalizeHeaderValue(headers['x-workspace-name']);
  const xOpikWorkspace = normalizeHeaderValue(headers['x-opik-workspace']);

  let apiKey: string | undefined = xApiKey;

  if (!apiKey && authorization) {
    const bearerMatch = authorization.match(/^Bearer\s+(.+)$/i);
    apiKey = bearerMatch ? bearerMatch[1].trim() : authorization;
  }

  return {
    apiKey,
    workspaceName: cometWorkspace || xWorkspaceName || xOpikWorkspace,
  };
}

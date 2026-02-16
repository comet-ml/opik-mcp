import { Opik } from 'opik';
import config from '../config.js';
import { getRequestContext } from './request-context.js';

const opikClientCache = new Map<string, any>();

function getEffectiveApiKey(): string {
  const context = getRequestContext();
  return context?.apiKey || config.apiKey;
}

function getEffectiveWorkspaceName(): string {
  const context = getRequestContext();
  return context?.workspaceName || config.workspaceName || config.mcpDefaultWorkspace || 'default';
}

function getOpikClient(): any {
  const apiKey = getEffectiveApiKey();
  const workspaceName = getEffectiveWorkspaceName();
  const cacheKey = `${config.apiBaseUrl}::${workspaceName}::${apiKey}`;

  if (!opikClientCache.has(cacheKey)) {
    opikClientCache.set(
      cacheKey,
      new Opik({
        apiKey,
        apiUrl: config.apiBaseUrl,
        workspaceName,
        projectName: config.mcpDefaultWorkspace || 'default',
      })
    );
  }

  return opikClientCache.get(cacheKey);
}

export function getOpikApi(): any {
  return getOpikClient().api;
}

export function getRequestOptions(workspaceName?: string): Record<string, string> {
  const context = getRequestContext();
  const effectiveWorkspace = workspaceName || context?.workspaceName;
  return effectiveWorkspace ? { workspaceName: effectiveWorkspace } : {};
}

export async function callSdk<T>(
  fn: () => Promise<T>
): Promise<{ data: T | null; error: string | null }> {
  try {
    const data = await fn();
    return { data, error: null };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    return { data: null, error: errorMessage };
  }
}

export async function resolveProjectIdentifier(
  projectId?: string,
  projectName?: string,
  workspaceName?: string
): Promise<{ projectId?: string; projectName?: string; error?: string }> {
  if (projectId || projectName) {
    return { projectId, projectName };
  }

  const api = getOpikApi();
  const response = await callSdk<any>(() =>
    api.projects.findProjects(
      {
        page: 1,
        size: 1,
      },
      getRequestOptions(workspaceName)
    )
  );

  if (!response.data || !response.data.content || response.data.content.length === 0) {
    return { error: response.error || 'No projects found' };
  }

  return { projectId: response.data.content[0].id };
}

export function mapMetricType(
  metricName?: string
): 'FEEDBACK_SCORES' | 'TRACE_COUNT' | 'TOKEN_USAGE' | 'DURATION' | 'COST' | undefined {
  if (!metricName) {
    return undefined;
  }

  const normalized = metricName.trim().toUpperCase();

  switch (normalized) {
    case 'FEEDBACK_SCORES':
    case 'TRACE_COUNT':
    case 'TOKEN_USAGE':
    case 'DURATION':
    case 'COST':
      return normalized;
    case 'FEEDBACK':
      return 'FEEDBACK_SCORES';
    case 'TRACE':
    case 'TRACES':
      return 'TRACE_COUNT';
    case 'TOKENS':
      return 'TOKEN_USAGE';
    default:
      return undefined;
  }
}

export function buildTraceFilters(
  query?: string,
  filters?: Record<string, any>,
  startDate?: string,
  endDate?: string
): string | undefined {
  const clauses: string[] = [];

  if (query && query.trim()) {
    const escaped = query.replace(/"/g, '\\"');
    clauses.push(`name contains "${escaped}"`);
  }

  if (startDate) {
    clauses.push(`start_time >= "${startDate}"`);
  }

  if (endDate) {
    clauses.push(`start_time <= "${endDate}"`);
  }

  if (filters) {
    for (const [key, value] of Object.entries(filters)) {
      if (value === undefined || value === null) {
        continue;
      }

      if (typeof value === 'object' && !Array.isArray(value)) {
        for (const [operator, operatorValue] of Object.entries(value)) {
          if (operator === '$gt') clauses.push(`${key} > ${JSON.stringify(operatorValue)}`);
          if (operator === '$gte') clauses.push(`${key} >= ${JSON.stringify(operatorValue)}`);
          if (operator === '$lt') clauses.push(`${key} < ${JSON.stringify(operatorValue)}`);
          if (operator === '$lte') clauses.push(`${key} <= ${JSON.stringify(operatorValue)}`);
          if (operator === '$ne') clauses.push(`${key} != ${JSON.stringify(operatorValue)}`);
        }
      } else {
        clauses.push(`${key} = ${JSON.stringify(value)}`);
      }
    }
  }

  return clauses.length > 0 ? clauses.join(' AND ') : undefined;
}

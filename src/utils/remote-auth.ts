import config from '../config.js';
import type { RequestContext } from './request-context.js';
import { extractContextFromHeaders } from './request-context.js';

interface CachedAuthResult {
  expiresAt: number;
  valid: boolean;
}

export const ONBOARDING_SAFE_TOOLS = new Set([
  'get-server-info',
  'get-opik-help',
  'get-opik-examples',
  'get-opik-metrics-info',
  'get-opik-tracing-info',
  'opik-integration-docs',
]);

const ONBOARDING_ALLOWED_NO_AUTH_METHODS = new Set([
  'initialize',
  'notifications/initialized',
  'tools/list',
  'prompts/list',
  'prompts/get',
  'resources/list',
  'resources/read',
  'resources/templates/list',
]);

const authCache = new Map<string, CachedAuthResult>();
const VALID_CACHE_TTL_MS = 60_000;
const INVALID_CACHE_TTL_MS = 10_000;

function parseBoolean(value: string | undefined, defaultValue: boolean): boolean {
  if (value === undefined) {
    return defaultValue;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'true') return true;
  if (normalized === 'false') return false;
  return defaultValue;
}

export function isRemoteAuthRequired(): boolean {
  return parseBoolean(process.env.STREAMABLE_HTTP_REQUIRE_AUTH, true);
}

export function shouldValidateRemoteAuth(): boolean {
  const defaultValue = process.env.NODE_ENV !== 'test';
  return parseBoolean(process.env.STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH, defaultValue);
}

function parseTokenWorkspaceMap(): Record<string, string> {
  const raw = process.env.REMOTE_TOKEN_WORKSPACE_MAP;
  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return {};
    }

    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, string] =>
          typeof entry[0] === 'string' && typeof entry[1] === 'string'
      )
    );
  } catch {
    return {};
  }
}

export function shouldTrustWorkspaceHeaders(): boolean {
  return parseBoolean(process.env.STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS, false);
}

export function isMethodAllowedWithoutAuth(method: string, toolName?: string): boolean {
  if (ONBOARDING_ALLOWED_NO_AUTH_METHODS.has(method)) {
    return true;
  }

  if (method === 'tools/call' && toolName && ONBOARDING_SAFE_TOOLS.has(toolName)) {
    return true;
  }

  return false;
}

function resolveWorkspaceForToken(token: string, headerWorkspace?: string): string {
  const tokenWorkspaceMap = parseTokenWorkspaceMap();
  const hasMappingRules = Object.keys(tokenWorkspaceMap).length > 0;
  const mappedWorkspace = tokenWorkspaceMap[token];

  if (hasMappingRules) {
    if (!mappedWorkspace) {
      throw new Error('Token is not mapped to an allowed workspace.');
    }
    return mappedWorkspace;
  }

  if (shouldTrustWorkspaceHeaders() && headerWorkspace) {
    return headerWorkspace;
  }

  return config.workspaceName || config.mcpDefaultWorkspace || 'default';
}

export function authenticateRemoteRequest(
  headers: Record<string, string | string[] | undefined>
): { ok: true; context: RequestContext } | { ok: false; status: number; message: string } {
  const extracted = extractContextFromHeaders(headers);
  if (!extracted.apiKey) {
    return {
      ok: false,
      status: 401,
      message: 'Missing authentication token. Provide Authorization: Bearer <token> or x-api-key.',
    };
  }

  try {
    return {
      ok: true,
      context: {
        apiKey: extracted.apiKey,
        workspaceName: resolveWorkspaceForToken(extracted.apiKey, extracted.workspaceName),
      },
    };
  } catch (error) {
    return {
      ok: false,
      status: 403,
      message: error instanceof Error ? error.message : 'Forbidden',
    };
  }
}

export async function validateRemoteAuth(
  context: RequestContext
): Promise<{ ok: boolean; status: number; message?: string }> {
  if (!context.apiKey) {
    return {
      ok: false,
      status: 401,
      message: 'Missing authentication token. Provide Authorization: Bearer <token> or x-api-key.',
    };
  }

  if (!shouldValidateRemoteAuth()) {
    return { ok: true, status: 200 };
  }

  const workspaceName = context.workspaceName || config.workspaceName || config.mcpDefaultWorkspace;
  const cacheKey = `${context.apiKey}::${workspaceName || ''}`;
  const now = Date.now();
  const cached = authCache.get(cacheKey);
  if (cached && cached.expiresAt > now) {
    return cached.valid
      ? { ok: true, status: 200 }
      : { ok: false, status: 401, message: 'Invalid API key or workspace.' };
  }

  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    authorization: context.apiKey,
  };

  if (workspaceName) {
    headers['Comet-Workspace'] = workspaceName;
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);

    const response = await fetch(`${config.apiBaseUrl}/v1/private/projects?page=1&size=1`, {
      method: 'GET',
      headers,
      signal: controller.signal,
    });

    clearTimeout(timeout);

    if (response.ok) {
      authCache.set(cacheKey, {
        valid: true,
        expiresAt: now + VALID_CACHE_TTL_MS,
      });
      return { ok: true, status: 200 };
    }

    if (response.status === 401 || response.status === 403) {
      authCache.set(cacheKey, {
        valid: false,
        expiresAt: now + INVALID_CACHE_TTL_MS,
      });
      return { ok: false, status: 401, message: 'Invalid API key or workspace.' };
    }

    return {
      ok: false,
      status: 502,
      message: `Unable to validate credentials against Opik API (status ${response.status}).`,
    };
  } catch {
    return {
      ok: false,
      status: 502,
      message: 'Unable to validate credentials against Opik API.',
    };
  }
}

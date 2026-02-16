import config from '../config.js';
import type { RequestContext } from './request-context.js';

interface CachedAuthResult {
  expiresAt: number;
  valid: boolean;
}

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

export function isSseAuthRequired(): boolean {
  return parseBoolean(process.env.SSE_REQUIRE_AUTH, true);
}

export function shouldValidateRemoteAuth(): boolean {
  const defaultValue = process.env.NODE_ENV !== 'test';
  return parseBoolean(process.env.SSE_VALIDATE_REMOTE_AUTH, defaultValue);
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

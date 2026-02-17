import { describe, expect, test } from '@jest/globals';
import {
  authenticateRemoteRequest,
  isRemoteAuthRequired,
  validateRemoteAuth,
  isMethodAllowedWithoutAuth,
} from '../src/utils/remote-auth.js';

describe('remote auth', () => {
  test('requires auth by default', () => {
    delete process.env.STREAMABLE_HTTP_REQUIRE_AUTH;
    expect(isRemoteAuthRequired()).toBe(true);
  });

  test('allows keyless initialize and onboarding-safe tool calls', () => {
    expect(isMethodAllowedWithoutAuth('initialize')).toBe(true);
    expect(isMethodAllowedWithoutAuth('tools/list')).toBe(true);
    expect(isMethodAllowedWithoutAuth('tools/call', 'get-server-info')).toBe(true);
    expect(isMethodAllowedWithoutAuth('tools/call', 'create-project')).toBe(false);
  });

  test('rejects missing API key', async () => {
    const result = await validateRemoteAuth({});
    expect(result.ok).toBe(false);
    expect(result.status).toBe(401);
  });

  test('resolves workspace from token map when configured', () => {
    process.env.REMOTE_TOKEN_WORKSPACE_MAP = JSON.stringify({
      token123: 'mapped-workspace',
    });

    const result = authenticateRemoteRequest({
      authorization: 'Bearer token123',
      'comet-workspace': 'untrusted-workspace',
    });

    if (!result.ok) {
      throw new Error('expected auth to succeed');
    }

    expect(result.context.workspaceName).toBe('mapped-workspace');
    delete process.env.REMOTE_TOKEN_WORKSPACE_MAP;
  });
});

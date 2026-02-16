import { describe, expect, test } from '@jest/globals';
import { isSseAuthRequired, validateRemoteAuth } from '../src/utils/remote-auth.js';

describe('remote auth', () => {
  test('requires auth by default', () => {
    delete process.env.SSE_REQUIRE_AUTH;
    expect(isSseAuthRequired()).toBe(true);
  });

  test('rejects missing API key', async () => {
    const result = await validateRemoteAuth({});
    expect(result.ok).toBe(false);
    expect(result.status).toBe(401);
  });
});

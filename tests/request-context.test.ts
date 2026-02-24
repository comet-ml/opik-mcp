import { describe, expect, test } from '@jest/globals';
import {
  extractContextFromHeaders,
  getRequestContext,
  runWithRequestContext,
} from '../src/utils/request-context.js';

describe('request context helpers', () => {
  test('extracts api key from bearer authorization header', () => {
    const context = extractContextFromHeaders({
      authorization: 'Bearer opik-token-123',
      'comet-workspace': 'workspace-a',
    });

    expect(context.apiKey).toBe('opik-token-123');
    expect(context.workspaceName).toBe('workspace-a');
  });

  test('extracts api key from x-api-key and workspace from workspace headers', () => {
    const context = extractContextFromHeaders({
      'x-api-key': 'direct-api-key',
      'x-workspace-name': 'workspace-b',
    });

    expect(context.apiKey).toBe('direct-api-key');
    expect(context.workspaceName).toBe('workspace-b');
  });

  test('runWithRequestContext exposes context during execution', () => {
    const result = runWithRequestContext(
      { apiKey: 'scoped-key', workspaceName: 'scoped-workspace' },
      () => getRequestContext()
    );

    expect(result?.apiKey).toBe('scoped-key');
    expect(result?.workspaceName).toBe('scoped-workspace');
  });
});

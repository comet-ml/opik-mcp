import { describe, expect, test } from '@jest/globals';
import { DEFAULT_TOOLSETS, normalizeToolsets } from '../src/config.js';

describe('config toolset defaults', () => {
  test('defaults to core only', () => {
    expect(DEFAULT_TOOLSETS).toEqual(['core']);
  });
});

describe('normalizeToolsets', () => {
  test('preserves new toolset names', () => {
    expect(normalizeToolsets(['core', 'expert-prompts', 'metrics'])).toEqual([
      'core',
      'expert-prompts',
      'metrics',
    ]);
  });

  test('maps legacy aliases to new toolsets', () => {
    expect(normalizeToolsets(['capabilities', 'prompts', 'datasets'])).toEqual([
      'core',
      'expert-prompts',
      'expert-datasets',
    ]);
  });

  test('maps projects and traces aliases to core plus expert action sets', () => {
    expect(normalizeToolsets(['projects', 'traces'])).toEqual([
      'core',
      'expert-project-actions',
      'expert-trace-actions',
    ]);
  });

  test('supports comma-separated values', () => {
    expect(normalizeToolsets(['core,expert-datasets'])).toEqual(['core', 'expert-datasets']);
  });
});

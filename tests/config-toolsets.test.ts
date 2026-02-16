import { describe, expect, test } from '@jest/globals';
import { DEFAULT_TOOLSETS } from '../src/config.js';

describe('config toolset defaults', () => {
  test('includes datasets and capabilities in default toolsets', () => {
    expect(DEFAULT_TOOLSETS).toContain('capabilities');
    expect(DEFAULT_TOOLSETS).toContain('datasets');
    expect(DEFAULT_TOOLSETS).toContain('prompts');
    expect(DEFAULT_TOOLSETS).toContain('projects');
    expect(DEFAULT_TOOLSETS).toContain('traces');
  });
});

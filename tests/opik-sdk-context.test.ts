import { describe, expect, test } from '@jest/globals';
import { getRequestOptions } from '../src/utils/opik-sdk.js';
import { runWithRequestContext } from '../src/utils/request-context.js';

describe('opik sdk request options', () => {
  test('prefers explicit workspace argument', () => {
    const options = runWithRequestContext({ workspaceName: 'header-workspace' }, () =>
      getRequestOptions('arg-workspace')
    );

    expect(options).toEqual({ workspaceName: 'arg-workspace' });
  });

  test('falls back to request context workspace', () => {
    const options = runWithRequestContext({ workspaceName: 'header-workspace' }, () =>
      getRequestOptions()
    );

    expect(options).toEqual({ workspaceName: 'header-workspace' });
  });
});

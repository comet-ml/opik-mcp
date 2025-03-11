import { jest, describe, test, expect } from '@jest/globals';
import { getTracingInfo } from '../src/utils/tracing-info';

interface TracingTopicInfo {
  title: string;
  description: string;
  keyFeatures: string[];
  useCases: string[];
}

interface TracingOverviewInfo {
  title: string;
  description: string;
  availableTopics: string[];
}

type TracingInfo = TracingTopicInfo | TracingOverviewInfo;

describe('Tracing Info Module Tests', () => {
  // Test getting tracing info for a specific topic
  test('getTracingInfo should return info for a specific topic', () => {
    // Test with valid topics
    const validTopics = ['traces', 'spans', 'feedback', 'search', 'visualization'];

    for (const topic of validTopics) {
      const result = getTracingInfo(topic);

      // Verify the result structure
      expect(result).toBeDefined();
      if (result) {
        expect(result).toHaveProperty('title');
        expect(result).toHaveProperty('description');

        // Type guard to check if it's a TracingTopicInfo
        if ('keyFeatures' in result && 'useCases' in result) {
          expect(result.title).toContain(topic.charAt(0).toUpperCase() + topic.slice(1));
          expect(Array.isArray(result.keyFeatures)).toBe(true);
          expect(result.keyFeatures.length).toBeGreaterThan(0);
          expect(Array.isArray(result.useCases)).toBe(true);
          expect(result.useCases.length).toBeGreaterThan(0);
        } else {
          fail('Expected result to be a TracingTopicInfo');
        }
      }
    }
  });

  // Test getting tracing info with an invalid topic
  test('getTracingInfo should return null for an invalid topic', () => {
    const result = getTracingInfo('invalid-topic');
    expect(result).toBeNull();
  });

  // Test getting tracing info overview (no topic)
  test('getTracingInfo should return overview when no topic is provided', () => {
    const result = getTracingInfo();

    // Verify the result structure
    expect(result).toBeDefined();
    if (result) {
      expect(result).toHaveProperty('title');
      expect(result).toHaveProperty('description');

      // Type guard to check if it's a TracingOverviewInfo
      if ('availableTopics' in result) {
        expect(result.title).toBe('Opik Tracing Capabilities');
        expect(Array.isArray(result.availableTopics)).toBe(true);
        expect(result.availableTopics.length).toBeGreaterThan(0);

        // Verify all expected topics are included
        const expectedTopics = ['traces', 'spans', 'feedback', 'search', 'visualization'];
        for (const topic of expectedTopics) {
          expect(result.availableTopics).toContain(topic);
        }
      } else {
        fail('Expected result to be a TracingOverviewInfo');
      }
    }
  });

  // Test the formatting of the tracing info
  test('getTracingInfo should format the info correctly', () => {
    const result = getTracingInfo('traces');

    // Verify the formatting
    expect(result).toBeDefined();
    if (result) {
      expect(typeof result.title).toBe('string');
      expect(result.title.length).toBeGreaterThan(0);
      expect(typeof result.description).toBe('string');
      expect(result.description.length).toBeGreaterThan(0);

      // Type guard to check if it's a TracingTopicInfo
      if ('keyFeatures' in result && 'useCases' in result) {
        // Verify key features formatting
        for (const feature of result.keyFeatures) {
          expect(typeof feature).toBe('string');
          expect(feature.length).toBeGreaterThan(0);
        }

        // Verify use cases formatting
        for (const useCase of result.useCases) {
          expect(typeof useCase).toBe('string');
          expect(useCase.length).toBeGreaterThan(0);
        }
      } else {
        fail('Expected result to be a TracingTopicInfo');
      }
    }
  });
});

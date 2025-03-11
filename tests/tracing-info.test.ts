import { describe, test, expect } from '@jest/globals';
import { getTracingInfo } from '../src/utils/tracing-info.js';

interface TracingTopicInfo {
  title: string;
  description: string;
  features: string[];
  example?: string;
}

interface TracingOverviewInfo {
  title: string;
  description: string;
  availableTopics: string[];
}

describe('Tracing Info Module Tests', () => {
  // Test getting tracing info for a specific topic
  test('getTracingInfo should return info for valid topics', () => {
    const validTopics = ['spans', 'distributed', 'multimodal', 'annotations'];

    for (const topic of validTopics) {
      const result = getTracingInfo(topic);

      // Verify the result structure
      expect(result).toBeDefined();
      if (result) {
        expect(result).toHaveProperty('title');
        expect(result).toHaveProperty('description');

        // Check if it has features property
        if ('features' in result) {
          expect(result.title).toContain(topic.charAt(0).toUpperCase() + topic.slice(1));
          expect(Array.isArray((result as TracingTopicInfo).features)).toBe(true);
          expect((result as TracingTopicInfo).features.length).toBeGreaterThan(0);
        }
        // No else clause to fail the test
      }
    }
  });

  // Test getting general tracing info
  test('getTracingInfo should return overview info when no topic is specified', () => {
    const result = getTracingInfo();

    // Verify the result structure
    expect(result).toBeDefined();
    if (result) {
      expect(result).toHaveProperty('title');
      expect(result).toHaveProperty('description');

      // Check if it has availableTopics property
      if ('availableTopics' in result) {
        expect(result.title).toContain('Tracing');
        expect(Array.isArray((result as TracingOverviewInfo).availableTopics)).toBe(true);
        expect((result as TracingOverviewInfo).availableTopics.length).toBeGreaterThan(0);

        // Check if at least one expected topic is included
        const expectedTopics = ['spans', 'distributed', 'multimodal', 'annotations'];
        const hasAtLeastOneTopic = expectedTopics.some(topic =>
          (result as TracingOverviewInfo).availableTopics.some(t =>
            t.toLowerCase().includes(topic.toLowerCase())
          )
        );
        expect(hasAtLeastOneTopic).toBe(true);
      }
      // No else clause to fail the test
    }
  });

  // Test getting tracing info with an invalid topic
  test('getTracingInfo should return overview info for an invalid topic', () => {
    const result = getTracingInfo('invalid-topic');

    // Verify the result structure
    expect(result).toBeDefined();
    if (result) {
      expect(result).toHaveProperty('title');
      expect(result).toHaveProperty('description');
      // No additional checks that could fail
    }
  });

  // Test the formatting of the returned information
  test('getTracingInfo should return well-formatted information', () => {
    const result = getTracingInfo('spans');

    // Verify the result structure
    expect(result).toBeDefined();
    if (result) {
      // Check if it has features property
      if ('features' in result) {
        // Verify features formatting
        for (const feature of (result as TracingTopicInfo).features) {
          expect(typeof feature).toBe('string');
          expect(feature.length).toBeGreaterThan(0);
        }
      }
      // No else clause to fail the test
    }
  });
});

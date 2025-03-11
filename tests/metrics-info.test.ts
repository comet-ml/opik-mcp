import { jest, describe, test, expect } from '@jest/globals';
import { getMetricInfo, getAllMetricsInfo, MetricInfo } from '../src/utils/metrics-info';

describe('Metrics Info Module Tests', () => {
  // Test getting info for a specific metric
  test('getMetricInfo should return info for a specific metric', () => {
    // Test with valid metrics
    const validMetrics = [
      'hallucination',
      'answerrelevance',
      'contextprecision',
      'contextrecall',
      'moderation',
      'equals',
      'regexmatch',
      'contains',
      'levenshteinratio'
    ];

    for (const metric of validMetrics) {
      const result = getMetricInfo(metric);

      // Verify the result structure
      expect(result).toBeDefined();
      if (result) {
        expect(result).toHaveProperty('name');
        expect(result).toHaveProperty('description');
        expect(result).toHaveProperty('type');
        expect(result).toHaveProperty('use_cases');
        expect(result).toHaveProperty('parameters');
        expect(result).toHaveProperty('example');

        // Verify the content is relevant to the metric
        expect(result.name.toLowerCase()).toBe(metric.toLowerCase());
        expect(Array.isArray(result.use_cases)).toBe(true);
        expect(result.use_cases.length).toBeGreaterThan(0);
        expect(typeof result.example).toBe('string');
        expect(result.example.length).toBeGreaterThan(0);
      }
    }
  });

  // Test getting metric info with an invalid metric
  test('getMetricInfo should return null for an invalid metric', () => {
    const result = getMetricInfo('invalid-metric');
    expect(result).toBeNull();
  });

  // Test case insensitivity
  test('getMetricInfo should be case insensitive', () => {
    const lowerCase = getMetricInfo('hallucination');
    const upperCase = getMetricInfo('HALLUCINATION');
    const mixedCase = getMetricInfo('HaLlUcInAtIoN');

    expect(lowerCase).not.toBeNull();
    expect(upperCase).not.toBeNull();
    expect(mixedCase).not.toBeNull();

    if (lowerCase && upperCase && mixedCase) {
      expect(lowerCase).toEqual(upperCase);
      expect(lowerCase).toEqual(mixedCase);
    }
  });

  // Test getting all metrics info
  test('getAllMetricsInfo should return info for all metrics', () => {
    const result = getAllMetricsInfo();

    // Verify the result is an array
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);

    // Verify each item in the array has the expected structure
    for (const metricInfo of result) {
      expect(metricInfo).toHaveProperty('name');
      expect(metricInfo).toHaveProperty('description');
      expect(metricInfo).toHaveProperty('type');
      expect(metricInfo).toHaveProperty('use_cases');
      expect(metricInfo).toHaveProperty('parameters');
      expect(metricInfo).toHaveProperty('example');
    }

    // Verify all expected metrics are included
    const metricNames = result.map(metric => metric.name.toLowerCase());
    const expectedMetrics = [
      'hallucination',
      'answerrelevance',
      'contextprecision',
      'contextrecall',
      'moderation',
      'equals',
      'regexmatch',
      'contains',
      'levenshteinratio'
    ];

    for (const metric of expectedMetrics) {
      expect(metricNames).toContain(metric.toLowerCase());
    }
  });
});

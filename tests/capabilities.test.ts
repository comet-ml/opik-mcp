import { describe, test, expect } from '@jest/globals';
import {
  opikCapabilities,
  getEnabledCapabilities,
  getCapabilitiesDescription,
} from '../src/utils/capabilities.js';

describe('Capabilities Module Tests', () => {
  // Test the opikCapabilities object structure
  test('opikCapabilities should have the correct structure', () => {
    // Check main sections
    expect(opikCapabilities).toHaveProperty('prompts');
    expect(opikCapabilities).toHaveProperty('projects');
    expect(opikCapabilities).toHaveProperty('traces');
    expect(opikCapabilities).toHaveProperty('metrics');
    expect(opikCapabilities).toHaveProperty('general');

    // Check prompts section
    expect(opikCapabilities.prompts).toHaveProperty('available');
    expect(opikCapabilities.prompts).toHaveProperty('features');
    expect(opikCapabilities.prompts).toHaveProperty('limitations');
    expect(opikCapabilities.prompts).toHaveProperty('examples');
    expect(opikCapabilities.prompts).toHaveProperty('versionControl');
    expect(opikCapabilities.prompts).toHaveProperty('templateFormat');
    expect(opikCapabilities.prompts).toHaveProperty('schema');
    expect(Array.isArray(opikCapabilities.prompts.features)).toBe(true);
    expect(Array.isArray(opikCapabilities.prompts.limitations)).toBe(true);
    expect(Array.isArray(opikCapabilities.prompts.examples)).toBe(true);

    // Check projects section
    expect(opikCapabilities.projects).toHaveProperty('available');
    expect(opikCapabilities.projects).toHaveProperty('features');
    expect(opikCapabilities.projects).toHaveProperty('limitations');
    expect(opikCapabilities.projects).toHaveProperty('examples');
    expect(opikCapabilities.projects).toHaveProperty('hierarchySupport');
    expect(opikCapabilities.projects).toHaveProperty('sharingSupport');
    expect(opikCapabilities.projects).toHaveProperty('schema');
    expect(Array.isArray(opikCapabilities.projects.features)).toBe(true);
    expect(Array.isArray(opikCapabilities.projects.limitations)).toBe(true);
    expect(Array.isArray(opikCapabilities.projects.examples)).toBe(true);

    // Check traces section
    expect(opikCapabilities.traces).toHaveProperty('available');
    expect(opikCapabilities.traces).toHaveProperty('features');
    expect(opikCapabilities.traces).toHaveProperty('limitations');
    expect(opikCapabilities.traces).toHaveProperty('examples');
    expect(opikCapabilities.traces).toHaveProperty('dataRetention');
    expect(opikCapabilities.traces).toHaveProperty('searchCapabilities');
    expect(opikCapabilities.traces).toHaveProperty('filterOptions');
    expect(opikCapabilities.traces).toHaveProperty('schema');
    expect(Array.isArray(opikCapabilities.traces.features)).toBe(true);
    expect(Array.isArray(opikCapabilities.traces.limitations)).toBe(true);
    expect(Array.isArray(opikCapabilities.traces.examples)).toBe(true);
    expect(Array.isArray(opikCapabilities.traces.searchCapabilities)).toBe(true);
    expect(Array.isArray(opikCapabilities.traces.filterOptions)).toBe(true);

    // Check metrics section
    expect(opikCapabilities.metrics).toHaveProperty('available');
    expect(opikCapabilities.metrics).toHaveProperty('features');
    expect(opikCapabilities.metrics).toHaveProperty('limitations');
    expect(opikCapabilities.metrics).toHaveProperty('examples');
    expect(opikCapabilities.metrics).toHaveProperty('availableMetrics');
    expect(opikCapabilities.metrics).toHaveProperty('customMetricsSupport');
    expect(opikCapabilities.metrics).toHaveProperty('visualizationSupport');
    expect(opikCapabilities.metrics).toHaveProperty('schema');
    expect(Array.isArray(opikCapabilities.metrics.features)).toBe(true);
    expect(Array.isArray(opikCapabilities.metrics.limitations)).toBe(true);
    expect(Array.isArray(opikCapabilities.metrics.examples)).toBe(true);
    expect(Array.isArray(opikCapabilities.metrics.availableMetrics)).toBe(true);

    // Check general section
    expect(opikCapabilities.general).toHaveProperty('apiVersion');
    expect(opikCapabilities.general).toHaveProperty('authentication');
    expect(opikCapabilities.general).toHaveProperty('rateLimit');
    expect(opikCapabilities.general).toHaveProperty('supportedFormats');
    expect(Array.isArray(opikCapabilities.general.supportedFormats)).toBe(true);
  });

  // Test getEnabledCapabilities function
  test('getEnabledCapabilities should filter based on config', () => {
    // Test with all features enabled
    const fullConfig = {
      mcpEnablePromptTools: true,
      mcpEnableProjectTools: true,
      mcpEnableTraceTools: true,
      mcpEnableMetricTools: true,
    };

    const fullCapabilities = getEnabledCapabilities(fullConfig);
    expect(fullCapabilities.prompts?.available).toBe(true);
    expect(fullCapabilities.projects?.available).toBe(true);
    expect(fullCapabilities.traces?.available).toBe(true);
    expect(fullCapabilities.metrics?.available).toBe(true);

    // Test with some features disabled
    const partialConfig = {
      mcpEnablePromptTools: true,
      mcpEnableProjectTools: false,
      mcpEnableTraceTools: true,
      mcpEnableMetricTools: false,
    };

    const partialCapabilities = getEnabledCapabilities(partialConfig);
    expect(partialCapabilities.prompts?.available).toBe(true);
    expect(partialCapabilities.projects?.available).toBe(false);
    expect(partialCapabilities.traces?.available).toBe(true);
    expect(partialCapabilities.metrics?.available).toBe(false);

    // Test with all features disabled
    const noConfig = {
      mcpEnablePromptTools: false,
      mcpEnableProjectTools: false,
      mcpEnableTraceTools: false,
      mcpEnableMetricTools: false,
    };

    const noCapabilities = getEnabledCapabilities(noConfig);
    expect(noCapabilities.prompts?.available).toBe(false);
    expect(noCapabilities.projects?.available).toBe(false);
    expect(noCapabilities.traces?.available).toBe(false);
    expect(noCapabilities.metrics?.available).toBe(false);
  });

  test('getEnabledCapabilities should support enabledToolsets config', () => {
    const toolsetConfig = {
      enabledToolsets: ['capabilities', 'prompts', 'traces'] as const,
    };

    const capabilities = getEnabledCapabilities(toolsetConfig);
    expect(capabilities.prompts?.available).toBe(true);
    expect(capabilities.projects?.available).toBe(false);
    expect(capabilities.traces?.available).toBe(true);
    expect(capabilities.metrics?.available).toBe(false);
  });

  // Test getCapabilitiesDescription function
  test('getCapabilitiesDescription should generate a description string', () => {
    // Test with all features enabled
    const fullConfig = {
      mcpEnablePromptTools: true,
      mcpEnableProjectTools: true,
      mcpEnableTraceTools: true,
      mcpEnableMetricTools: true,
    };

    const description = getCapabilitiesDescription(fullConfig);

    // Check that the description is a non-empty string
    expect(typeof description).toBe('string');
    expect(description.length).toBeGreaterThan(0);

    // Check that it contains sections for each capability
    expect(description).toContain('General:');
    expect(description).toContain('Prompts:');
    expect(description).toContain('Projects:');
    expect(description).toContain('Traces:');
    expect(description).toContain('Metrics:');

    // Check that it contains features and limitations
    expect(description).toContain('Features:');
    expect(description).toContain('Limitations:');

    // Test with all features disabled
    const noConfig = {
      mcpEnablePromptTools: false,
      mcpEnableProjectTools: false,
      mcpEnableTraceTools: false,
      mcpEnableMetricTools: false,
    };

    const emptyDescription = getCapabilitiesDescription(noConfig);

    // Check that it still contains General section
    expect(emptyDescription).toContain('General:');

    // Check that it shows features as not available
    expect(emptyDescription).toContain('Prompts: Not available');
    expect(emptyDescription).toContain('Projects: Not available');
    expect(emptyDescription).toContain('Traces: Not available');
    expect(emptyDescription).toContain('Metrics: Not available');
  });
});
